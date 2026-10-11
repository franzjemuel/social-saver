"""Real database/worker tests; opt in only with the disposable runner's DSN."""
import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from core.database import Database
from core.queue import JobQueue
from core.repository import ProfileImportIdempotencyConflict, Repository
from providers.base import ArchiveMediaAsset, ArchivedPost
from ops import staging_canary

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get('TEST_DATABASE_URL')
pytestmark = [pytest.mark.skipif(not DSN, reason='run scripts/test-integration.sh for disposable Postgres/PGMQ'),
              pytest.mark.asyncio(loop_scope='module')]


@pytest_asyncio.fixture(scope='module', loop_scope='module')
async def database():
    url = urlsplit(DSN)
    # Never reuse DATABASE_URL or allow this fixture to migrate a hosted database.
    assert url.hostname in ('127.0.0.1', 'localhost')
    assert url.path == '/social_saver_test'
    con = await asyncpg.connect(DSN)
    try:
        assert await con.fetchval("select to_regclass('public.app_users')") is None, 'requires a fresh database'
        await con.execute('create role anon nologin; create role authenticated nologin;')
        migrations = sorted((ROOT / 'supabase/migrations').glob('*.sql'))
        assert [int(p.name.split('_')[0]) for p in migrations] == list(range(1, len(migrations) + 1))
        for migration in migrations:
            async with con.transaction():
                await con.execute(migration.read_text())
        assert len(migrations) >= 20
        assert await con.fetchval("select exists(select from pg_extension where extname='pgmq')")
        assert await con.fetchval("select exists(select from pg_extension where extname='pg_cron')")
        print(f'Applied {len(migrations)} unchanged migrations to disposable Postgres')
    finally:
        await con.close()
    db = Database(DSN)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def test_reproduce_unconfigured_driver_failure(database):
    """Actual baseline driver behavior, without mocking database calls."""
    con = await asyncpg.connect(DSN)
    try:
        with pytest.raises(asyncpg.DataError):
            await con.execute(
                "insert into worker_heartbeats(worker_id,service,metadata) values($1,$2,$3)",
                'unconfigured-probe', 'media-worker', {'pid': 1},
            )
        assert isinstance(await con.fetchval("select '{}'::jsonb"), str)
    finally:
        await con.close()


async def test_codecs_on_every_connection_and_replacement(database):
    values = [{'nested': [1, True, None, 'é']}, [1, {'key': 'value'}], 'plain text', 7, False, None]
    async with AsyncExitStack() as stack:
        connections = [await stack.enter_async_context(database.pool.acquire()) for _ in range(5)]
        assert len({c.get_server_pid() for c in connections}) == 5
        for con in connections:
            for sql_type in ('json', 'jsonb'):
                for value in values:
                    assert await con.fetchval(f'select $1::{sql_type}', value) == value
            # Serialized JSON is explicitly bound as SQL text, not a JSON string scalar.
            value = {'preencoded': True}
            assert await con.fetchval('select $1::text::jsonb', json.dumps(value)) == value
    await database.pool.expire_connections()
    assert await database.pool.fetchval('select $1::jsonb', {'replacement': True}) == {'replacement': True}


async def test_job_tenant_boundary_and_dead_letter(database):
    from apps.worker.main import dead_letter
    repo = Repository(database.pool)
    queue = JobQueue(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    payload = {'archive': True, 'nested': {'assets': [1, 2]}}
    job_id = await repo.create_job(owner, 0, 'test', payload)
    assert (await repo.get_job(job_id))['input'] == payload
    assert await repo.get_owned_job_status(other, job_id) is None
    assert (await repo.get_owned_job_status(owner, job_id))['id'] == job_id
    message_id = await queue.send(str(job_id))
    messages = await queue.claim()
    assert len(messages) == 1
    message = messages[0]
    assert message['msg_id'] == message_id
    assert message['message'] == {'version': 1, 'job_id': str(job_id)}
    await dead_letter(database, queue, repo, message, job_id, RuntimeError('synthetic integration failure'))
    event = await database.pool.fetchrow('select * from dead_letter_events where job_id=$1', job_id)
    assert event['payload']['original_message'] == message['message']
    assert (await repo.get_job(job_id))['status'] == 'failed'
    dead_queue = JobQueue(database.pool, 'dead_letter_jobs')
    dead = await dead_queue.claim()
    assert len(dead) == 1 and dead[0]['message']['job_id'] == str(job_id)
    await dead_queue.archive(dead[0]['msg_id'])


async def test_queue_extend_visibility_uses_unambiguous_pgmq_signature(database):
    """The v1.10 PGMQ single-message overload needs explicitly typed binds."""
    signatures = await database.pool.fetch(
        """select pg_get_function_identity_arguments(p.oid) as signature
           from pg_proc p
           join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'pgmq' and p.proname = 'set_vt'
           order by signature"""
    )
    assert 'queue_name text, msg_id bigint, vt integer' in {
        row['signature'] for row in signatures
    }

    queue = JobQueue(database.pool)
    job_id = str(uuid4())
    message_id = await queue.send(job_id)
    messages = await queue.claim(10)
    message = next(message for message in messages if message['msg_id'] == message_id)

    extended = await queue.extend_visibility(message['msg_id'], 60)
    assert extended is not None
    assert extended['msg_id'] == message_id
    await queue.archive(message_id)


async def test_profile_media_job_uuid_parameters_use_real_postgres(database):
    """Regression for UUID parameter inference in the profile-media queue query."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id, platform, platform_account_id, username)
           values($1, 'tiktok', 'profile-account', 'owner') returning id""",
        owner,
    )
    post = await database.pool.fetchval(
        """insert into archived_posts(archived_profile_id, platform_post_id, original_url, media_type)
           values($1, 'post-id', 'https://www.tiktok.com/@owner/video/post-id', 'video') returning id""",
        profile,
    )
    await database.pool.execute(
        """insert into archived_post_media_assets(archived_post_id, position, asset_type, source_url)
           values($1, 0, 'video', 'https://www.tiktok.com/@owner/video/post-id')""",
        post,
    )

    targeted_job = await repo.create_owned_profile_media_job(owner, 1, profile, post)
    assert targeted_job is not None and targeted_job.created is True
    assert (await repo.get_job(targeted_job.id))['input'] == {
        'profile_id': str(profile), 'post_id': str(post),
    }

    bulk_job = await repo.create_owned_profile_media_job(owner, 1, profile)
    assert bulk_job is not None and bulk_job.created is True
    assert (await repo.get_job(bulk_job.id))['input'] == {'profile_id': str(profile)}

    assert await repo.create_owned_profile_media_job(other, 2, profile, post) is None
    assert await repo.create_owned_profile_media_job(owner, 1, profile, uuid4()) is None


async def test_profile_photo_delivery_job_is_owned_complete_and_coalesced(database):
    """The delivery queue must never target another tenant or an incomplete carousel."""
    repo = Repository(database.pool)
    queue = JobQueue(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','delivery-account','owner') returning id""", owner,
    )
    post = await database.pool.fetchval(
        """insert into archived_posts(archived_profile_id,platform_post_id,original_url,media_type)
           values($1,'delivery-post','https://www.tiktok.com/@owner/photo/delivery-post','carousel')
           returning id""", profile,
    )
    objects = []
    for position in range(2):
        object_id = await database.pool.fetchval(
            """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
               values($1,$2,1,'image/jpeg') returning id""",
            f"photo-delivery-{post}-{position}", f"archive/test/photo-delivery-{position}.jpg",
        )
        objects.append(object_id)
        await database.pool.execute(
            """insert into archived_post_media_assets
                 (archived_post_id,position,asset_type,stored_object_id)
               values($1,$2,'photo',$3)""", post, position, object_id,
        )

    first, second = await asyncio.gather(
        repo.create_owned_profile_photo_delivery_job(owner, 1, profile, post, queue_name=queue.queue_name),
        repo.create_owned_profile_photo_delivery_job(owner, 1, profile, post, queue_name=queue.queue_name),
    )
    assert first is not None and second is not None
    assert first.id == second.id
    assert sorted((first.created, second.created)) == [False, True]
    assert (await repo.get_job(first.id))["input"] == {
        "profile_id": str(profile), "post_id": str(post), "asset_count": 2,
    }
    assert await repo.create_owned_profile_photo_delivery_job(other, 2, profile, post, queue_name=queue.queue_name) is None
    messages = await queue.claim()
    matches = [message for message in messages if message['message']['job_id'] == str(first.id)]
    assert len(matches) == 1
    await queue.archive(matches[0]['msg_id'])

    rows = await repo.list_owned_complete_profile_photo_delivery_assets(owner, profile, post)
    assert [row["position"] for row in rows] == [0, 1]
    assert await repo.list_owned_complete_profile_photo_delivery_assets(other, profile, post) == []
    await database.pool.execute(
        "update archived_post_media_assets set stored_object_id=null where archived_post_id=$1 and position=1", post,
    )
    assert await repo.create_owned_profile_photo_delivery_job(owner, 1, profile, post, queue_name=queue.queue_name) is None


async def test_profile_media_job_coalesces_concurrent_targeted_requests(database):
    """Double taps must produce one PGMQ job before a video is persisted."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','dedupe-account','owner') returning id""", owner,
    )
    post = await database.pool.fetchval(
        """insert into archived_posts(archived_profile_id,platform_post_id,original_url,media_type)
           values($1,'dedupe-post','https://www.tiktok.com/@owner/video/dedupe-post','video') returning id""",
        profile,
    )
    await database.pool.execute(
        """insert into archived_post_media_assets(archived_post_id,position,asset_type)
           values($1,0,'video')""", post,
    )
    queue = JobQueue(database.pool)
    first, second = await asyncio.gather(
        repo.create_owned_profile_media_job(owner, 1, profile, post, queue_name=queue.queue_name),
        repo.create_owned_profile_media_job(owner, 1, profile, post, queue_name=queue.queue_name),
    )
    assert first is not None and second is not None
    assert first.id == second.id
    assert sorted((first.created, second.created)) == [False, True]
    assert await database.pool.fetchval(
        "select count(*) from jobs where user_id=$1 and job_type='archive_profile_media'", owner,
    ) == 1
    messages = await queue.claim()
    matching = [message for message in messages if message['message']['job_id'] == str(first.id)]
    assert len(matching) == 1
    await queue.archive(matching[0]['msg_id'])


async def test_profile_import_validation_coalesces_tenant_target_and_request_retries(database):
    """Real Postgres/PGMQ coverage for validation locks and retry-key behavior."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    queue = JobQueue(database.pool)
    target = 'https://www.tiktok.com/@creator'

    first, second = await asyncio.gather(
        repo.create_owned_profile_import_validation_job(owner, 1, target, 'tap-a', queue_name=queue.queue_name),
        repo.create_owned_profile_import_validation_job(owner, 1, target, 'tap-b', queue_name=queue.queue_name),
    )
    assert first.id == second.id
    assert sorted((first.created, second.created)) == [False, True]
    retry = await repo.create_owned_profile_import_validation_job(
        owner, 1, target, 'tap-a', queue_name=queue.queue_name,
    )
    assert retry.id == first.id and retry.created is False
    with pytest.raises(ProfileImportIdempotencyConflict):
        await repo.create_owned_profile_import_validation_job(
            owner, 1, 'https://www.tiktok.com/@different', 'tap-a', queue_name=queue.queue_name,
        )
    with pytest.raises(ProfileImportIdempotencyConflict):
        await repo.create_owned_profile_import_validation_job(
            owner, 1, 'https://www.tiktok.com/@different', 'tap-b', queue_name=queue.queue_name,
        )
    separate_tenant = await repo.create_owned_profile_import_validation_job(
        other, 2, target, 'tap-a', queue_name=queue.queue_name,
    )
    assert separate_tenant.id != first.id and separate_tenant.created is True
    assert await database.pool.fetchval(
        "select count(*) from jobs where job_type='validate_profile_import'",
    ) == 2
    messages = await queue.claim(10)
    matching = [message for message in messages if message['message']['job_id'] in {str(first.id), str(separate_tenant.id)}]
    assert len(matching) == 2
    for message in matching:
        await queue.archive(message['msg_id'])
    assert await repo.get_owned_profile_import_validation(other, first.id) is None


async def test_profile_import_confirmation_is_atomic_tenant_scoped_and_coalesced(database):
    """Confirmation consumes only an owned completed validation and queues once."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    queue = JobQueue(database.pool)
    validation = await database.pool.fetchval(
        """insert into jobs(user_id,telegram_chat_id,job_type,status,progress,result,input,source_channel)
           values($1,1,'validate_profile_import','completed',100,$2,$3,'mini_app') returning id""",
        owner,
        {"platform": "tiktok", "target": "https://www.tiktok.com/@creator", "platform_account_id": "stable"},
        {"platform": "tiktok", "target": "https://www.tiktok.com/@creator"},
    )
    first, second = await asyncio.gather(
        repo.create_owned_profile_import_job(owner, 1, validation, queue_name=queue.queue_name),
        repo.create_owned_profile_import_job(owner, 1, validation, queue_name=queue.queue_name),
    )
    assert first is not None and second is not None and first.id == second.id
    assert sorted((first.created, second.created)) == [False, True]
    assert await repo.create_owned_profile_import_job(other, 2, validation, queue_name=queue.queue_name) is None
    assert await database.pool.fetchval(
        "select count(*) from jobs where user_id=$1 and job_type='import_profile'", owner,
    ) == 1
    job = await repo.get_job(first.id)
    assert job['input']['limit'] == 12
    assert job['input']['expected_platform_account_id'] == 'stable'
    messages = await queue.claim(10)
    matching = [message for message in messages if message['message']['job_id'] == str(first.id)]
    assert len(matching) == 1
    await queue.archive(matching[0]['msg_id'])

    # Existing same-tenant archive projects ready without queueing new work.
    await database.pool.execute(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','ready-stable','creator')""", owner,
    )
    ready_validation = await database.pool.fetchval(
        """insert into jobs(user_id,telegram_chat_id,job_type,status,progress,result,input,source_channel)
           values($1,1,'validate_profile_import','completed',100,$2,$3,'mini_app') returning id""",
        owner,
        {"platform": "tiktok", "target": "https://www.tiktok.com/@creator", "platform_account_id": "ready-stable"},
        {"platform": "tiktok", "target": "https://www.tiktok.com/@creator"},
    )
    ready = await repo.create_owned_profile_import_job(owner, 1, ready_validation, queue_name=queue.queue_name)
    assert ready is not None and ready.ready and ready.created
    assert (await repo.get_job(ready.id))['status'] == 'completed'

    # Separate previews resolving to the same stable account must share one
    # active import as well; locking only the validation job would race here.
    second_validation = await database.pool.fetchval(
        """insert into jobs(user_id,telegram_chat_id,job_type,status,progress,result,input,source_channel)
           values($1,1,'validate_profile_import','completed',100,$2,$3,'mini_app') returning id""",
        owner,
        {"platform": "tiktok", "target": "https://www.tiktok.com/@renamed", "platform_account_id": "second-stable"},
        {"platform": "tiktok", "target": "https://www.tiktok.com/@renamed"},
    )
    third_validation = await database.pool.fetchval(
        """insert into jobs(user_id,telegram_chat_id,job_type,status,progress,result,input,source_channel)
           values($1,1,'validate_profile_import','completed',100,$2,$3,'mini_app') returning id""",
        owner,
        {"platform": "tiktok", "target": "https://www.tiktok.com/@creator", "platform_account_id": "second-stable"},
        {"platform": "tiktok", "target": "https://www.tiktok.com/@creator"},
    )
    parallel = await asyncio.gather(
        repo.create_owned_profile_import_job(owner, 1, second_validation, queue_name=queue.queue_name),
        repo.create_owned_profile_import_job(owner, 1, third_validation, queue_name=queue.queue_name),
    )
    assert parallel[0] is not None and parallel[1] is not None
    assert parallel[0].id == parallel[1].id
    assert sorted((parallel[0].created, parallel[1].created)) == [False, True]


async def test_profile_media_playback_and_acquisition_are_tenant_scoped(database):
    """A foreign profile/post must be indistinguishable from absent repository data."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','playback-account','owner') returning id""", owner,
    )
    post = await database.pool.fetchval(
        """insert into archived_posts(archived_profile_id,platform_post_id,original_url,media_type,thumbnail_url)
           values($1,'playback-post','https://www.tiktok.com/@owner/video/playback-post','video',
                  'https://thumbnail.invalid/playback.jpg') returning id""",
        profile,
    )
    object_id = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
           values('a' || repeat('0',63),'private-object',1,'video/mp4') returning id""",
    )
    await database.pool.execute(
        """insert into archived_post_media_assets(archived_post_id,position,asset_type,stored_object_id)
           values($1,0,'video',$2)""", post, object_id,
    )
    photo_object_id = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
           values('b' || repeat('0',63),'private-photo-object',1,'image/jpeg') returning id""",
    )
    await database.pool.execute(
        """insert into archived_post_media_assets(archived_post_id,position,asset_type,stored_object_id)
           values($1,-1,'photo',$2)""", post, photo_object_id,
    )
    photo_asset = await database.pool.fetchval(
        """insert into archived_post_media_assets(archived_post_id,position,asset_type,source_url,metadata)
           values($1,1,'photo','https://images.example.invalid/pending.jpg',
                  '{"fallback_source_urls":["https://images.example.invalid/pending-alt.jpg"]}'::jsonb)
           returning id""",
        post,
    )
    owned_playback = await repo.get_owned_archived_post_playback(owner, profile, post)
    assert owned_playback['media_type'] == 'video'
    assert owned_playback['storage_key'] == 'private-object'
    assert owned_playback['thumbnail_url'] == 'https://thumbnail.invalid/playback.jpg'
    assert await repo.get_owned_archived_post_playback(other, profile, post) is None
    assert await repo.list_owned_archived_video_assets(other, profile, post) == []
    assert await repo.list_owned_archived_media_assets(other, profile, post) == []
    assert await repo.create_owned_profile_media_job(other, 2, profile, post) is None
    image_job = await repo.create_owned_profile_media_job(owner, 1, profile, post)
    assert image_job is not None and image_job.created is True
    images = await repo.list_owned_archived_media_assets(owner, profile, post)
    assert len(images) == 1 and images[0]["id"] == photo_asset
    assert images[0]["asset_type"] == "photo"
    assert images[0]["metadata"] == {"fallback_source_urls": ["https://images.example.invalid/pending-alt.jpg"]}
    await database.pool.execute('update stored_objects set deleted_at=now() where id=$1', object_id)
    deleted_playback = await repo.get_owned_archived_post_playback(owner, profile, post)
    assert deleted_playback is not None and deleted_playback['storage_key'] is None


async def test_profile_photo_asset_status_and_playback_targets_are_tenant_scoped(database):
    """Exercise ordered 0/N, partial, complete, pending, and deleted-photo state."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','photo-status-account','owner') returning id""",
        owner,
    )
    post = await database.pool.fetchval(
        """insert into archived_posts(archived_profile_id,platform_post_id,original_url,media_type)
           values($1,'photo-status-post','https://www.tiktok.com/@owner/photo/photo-status-post','carousel')
           returning id""",
        profile,
    )
    # The integration database is shared across this module. Derive fixture
    # hashes from test-specific labels instead of reusing short synthetic
    # prefixes used by adjacent tests under the global SHA uniqueness rule.
    available_sha = hashlib.sha256(b'profile-photo-status-available-v1').hexdigest()
    deleted_sha = hashlib.sha256(b'profile-photo-status-deleted-v1').hexdigest()
    available_object = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
           values($1,'private-photo-available',1,'image/jpeg') returning id""",
        available_sha,
    )
    deleted_object = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type,deleted_at)
           values($1,'private-photo-deleted',1,'image/jpeg',now()) returning id""",
        deleted_sha,
    )
    for position in (0, 1, 2):
        await database.pool.execute(
            """insert into archived_post_media_assets(archived_post_id,position,asset_type,stored_object_id)
               values($1,$2,'photo',$3)""",
            post, position, None,
        )

    projected = await repo.list_owned_archived_profile_posts(owner, profile, limit=10, offset=0)
    photo = next(row for row in projected if row['id'] == post)
    assert (photo['total_photo_assets'], photo['persisted_photo_assets'], photo['pending_photo_assets']) == (3, 0, 3)
    assert photo['photo_backup_status'] == 'not_started'

    await database.pool.execute(
        """update archived_post_media_assets set stored_object_id=$2
           where archived_post_id=$1 and position=1""",
        post, available_object,
    )
    await database.pool.execute(
        """update archived_post_media_assets set stored_object_id=$2
           where archived_post_id=$1 and position=2""",
        post, deleted_object,
    )

    rows = await repo.list_owned_archived_post_photo_assets(owner, profile, post)
    assert [(row['position'], row['state'], row['content_type']) for row in rows] == [
        (0, 'pending', None), (1, 'available', 'image/jpeg'), (2, 'unavailable', None),
    ]
    assert await repo.list_owned_archived_post_photo_assets(other, profile, post) is None
    available = await repo.get_owned_archived_post_photo_asset(owner, profile, post, 1)
    assert available['state'] == 'available'
    assert available['storage_key'] == 'private-photo-available'
    assert (await repo.get_owned_archived_post_photo_asset(owner, profile, post, 0))['state'] == 'pending'
    assert (await repo.get_owned_archived_post_photo_asset(owner, profile, post, 2))['state'] == 'unavailable'
    assert (await repo.get_owned_archived_post_photo_asset(owner, profile, post, 9))['state'] == 'missing'
    assert await repo.get_owned_archived_post_photo_asset(other, profile, post, 1) is None

    projected = await repo.list_owned_archived_profile_posts(owner, profile, limit=10, offset=0)
    photo = next(row for row in projected if row['id'] == post)
    assert (photo['total_photo_assets'], photo['persisted_photo_assets'], photo['pending_photo_assets']) == (3, 1, 2)
    assert photo['photo_backup_status'] == 'partial'

    await database.pool.execute(
        """update archived_post_media_assets set stored_object_id=$2
           where archived_post_id=$1 and position in (0,2)""",
        post, available_object,
    )
    projected = await repo.list_owned_archived_profile_posts(owner, profile, limit=10, offset=0)
    photo = next(row for row in projected if row['id'] == post)
    assert (photo['total_photo_assets'], photo['persisted_photo_assets'], photo['pending_photo_assets']) == (3, 3, 0)
    assert photo['photo_backup_status'] == 'complete'


async def test_profile_sync_jobs_and_presence_reconciliation_are_tenant_safe(database):
    """Exercise sync locking, PGMQ enqueue, transitions, and retained media in Postgres."""
    repo = Repository(database.pool); queue = JobQueue(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval("""insert into archived_profiles(user_id,platform,platform_account_id,username)
        values($1,'tiktok','sync-owner','owner') returning id""", owner)
    other_profile = await database.pool.fetchval("""insert into archived_profiles(user_id,platform,platform_account_id,username)
        values($1,'tiktok','sync-other','other') returning id""", other)
    first = await repo.create_owned_profile_sync_job(owner, 1, profile, queue_name=queue.queue_name)
    second = await repo.create_owned_profile_sync_job(owner, 1, profile, queue_name=queue.queue_name)
    assert first[0] == second[0] and first[1] is True and second[1] is False
    assert await repo.create_owned_profile_sync_job(other, 2, profile, queue_name=queue.queue_name) is None
    separate = await repo.create_owned_profile_sync_job(other, 2, other_profile, queue_name=queue.queue_name)
    assert separate[0] != first[0]
    assert await repo.get_owned_profile_sync(other, first[0]) is None
    messages = await queue.claim(10)
    matching = [m for m in messages if m['message']['job_id'] in {str(first[0]), str(separate[0])}]
    assert len(matching) == 2
    for message in matching: await queue.archive(message['msg_id'])

    states = [('present','video',True), ('removed','video',True), ('restored','video',False), ('still-removed','video',False)]
    post_ids = {}
    for platform_id, media_type, present in states:
        post_ids[platform_id] = await database.pool.fetchval("""insert into archived_posts
          (archived_profile_id,platform_post_id,original_url,media_type,is_present_on_original)
          values($1,$2,'https://example.invalid/' || $2,$3,$4) returning id""", profile, platform_id, media_type, present)
    object_id = await database.pool.fetchval("""insert into stored_objects(sha256,storage_key,size_bytes,content_type)
       values('c' || repeat('0',63),'sync-retained',1,'video/mp4') returning id""")
    await database.pool.execute("""insert into archived_post_media_assets(archived_post_id,position,asset_type,stored_object_id)
       values($1,0,'video',$2)""", post_ids['removed'], object_id)
    removed, restored = await repo.reconcile_archived_profile_presence(owner, profile, ['present','restored'])
    assert (removed, restored) == (1, 1)
    values = {row['platform_post_id']: row['is_present_on_original'] for row in await database.pool.fetch(
        'select platform_post_id,is_present_on_original from archived_posts where archived_profile_id=$1', profile)}
    assert values == {'present': True, 'removed': False, 'restored': True, 'still-removed': False}
    assert await repo.reconcile_archived_profile_presence(owner, profile, ['present','restored']) == (0, 0)
    playback = await repo.get_owned_archived_post_playback(owner, profile, post_ids['removed'])
    assert playback['storage_key'] == 'sync-retained'
    assert await database.pool.fetchval('select deleted_at is null from stored_objects where id=$1', object_id)
    posts = await repo.list_owned_archived_profile_posts(owner, profile)
    assert next(row for row in posts if row['platform_post_id'] == 'removed')['has_archived_media'] is True


async def test_full_profile_sync_repository_indexes_109_resumes_and_preserves_media(database):
    """Full-history primitives must scale past one API page without duplicating or detaching media."""
    repo = Repository(database.pool)
    queue = JobQueue(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','full-sync-owner','creator') returning id""",
        owner,
    )

    posts = [
        ArchivedPost(
            platform_post_id=str(index),
            original_url=f'https://www.tiktok.com/@creator/video/{index}',
            media_type='video',
            assets=[],
            caption=f'scanner {index}',
        )
        for index in range(109)
    ]
    inserted = await repo.insert_archived_posts_if_missing(owner, profile, posts)
    assert len(inserted) == 109
    assert await repo.insert_archived_posts_if_missing(owner, profile, posts) == set()

    first_page = await repo.list_owned_archived_profile_posts(owner, profile, limit=100, offset=0)
    second_page = await repo.list_owned_archived_profile_posts(owner, profile, limit=100, offset=100)
    assert len(first_page) == 100
    assert len(second_page) == 9
    assert len({row['platform_post_id'] for row in first_page + second_page}) == 109

    retained_post = await database.pool.fetchval(
        """select id from archived_posts where archived_profile_id=$1 and platform_post_id='0'""",
        profile,
    )
    object_id = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
           values('d' || repeat('0',63),'full-sync-retained',742,'video/mp4') returning id""",
    )
    await database.pool.execute(
        """insert into archived_post_media_assets(archived_post_id,position,asset_type,stored_object_id)
           values($1,0,'video',$2)""",
        retained_post, object_id,
    )
    _, created = await repo.upsert_archived_post(
        owner,
        profile,
        ArchivedPost(
            platform_post_id='0',
            original_url='https://www.tiktok.com/@creator/video/0',
            media_type='video',
            assets=[],
            caption='rich metadata',
        ),
    )
    assert created is False
    playback = await repo.get_owned_archived_post_playback(owner, profile, retained_post)
    assert playback['storage_key'] == 'full-sync-retained'
    assert await database.pool.fetchval(
        'select deleted_at is null from stored_objects where id=$1', object_id,
    )

    # Ordered photo assets use the same attachment table. Scanner hints may be
    # refreshed only before a position is attached to private archive bytes.
    # Once attached, preserving its original metadata prevents an upstream
    # reorder from silently reinterpreting those immutable archive bytes.
    inserted_photos = await repo.insert_archived_posts_if_missing(
        owner,
        profile,
        [ArchivedPost(
            platform_post_id='photo-carousel',
            original_url='https://www.tiktok.com/@creator/photo/photo-carousel',
            media_type='carousel',
            assets=[
                ArchiveMediaAsset(
                    0, 'photo', 'https://images.example.invalid/0.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/0-fallback.jpg']},
                ),
                ArchiveMediaAsset(
                    1, 'photo', 'https://images.example.invalid/1.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/1-fallback.jpg']},
                ),
                ArchiveMediaAsset(
                    2, 'photo', 'https://images.example.invalid/2.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/2-fallback.jpg']},
                ),
            ],
        )],
    )
    assert inserted_photos == {'photo-carousel'}
    photo_post_id = await database.pool.fetchval(
        """select id from archived_posts
           where archived_profile_id=$1 and platform_post_id='photo-carousel'""",
        profile,
    )
    photo_object_id = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
           values('e' || repeat('0',63),'full-sync-retained-photo',742,'image/jpeg') returning id""",
    )
    await database.pool.execute(
        """update archived_post_media_assets set stored_object_id=$2
           where archived_post_id=$1 and position=1""",
        photo_post_id, photo_object_id,
    )
    refreshed_photos = await repo.insert_archived_posts_if_missing(
        owner,
        profile,
        [ArchivedPost(
            platform_post_id='photo-carousel',
            original_url='https://www.tiktok.com/@creator/photo/photo-carousel',
            media_type='carousel',
            assets=[
                # A scan can report a changed order. Position 1 already has
                # immutable archive bytes, so its old scanner hints must win.
                ArchiveMediaAsset(
                    0, 'photo', 'https://images.example.invalid/1-refresh.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/1-refresh-fallback.jpg']},
                ),
                ArchiveMediaAsset(
                    1, 'photo', 'https://images.example.invalid/0-refresh.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/0-refresh-fallback.jpg']},
                ),
                ArchiveMediaAsset(
                    2, 'photo', 'https://images.example.invalid/2-refresh.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/2-refresh-fallback.jpg']},
                ),
                ArchiveMediaAsset(
                    3, 'photo', 'https://images.example.invalid/3-refresh.jpg',
                    metadata={'fallback_source_urls': ['https://images.example.invalid/3-refresh-fallback.jpg']},
                ),
            ],
        )],
    )
    assert refreshed_photos == set()
    photo_assets = await database.pool.fetch(
        """select position,source_url,metadata,stored_object_id from archived_post_media_assets
           where archived_post_id=$1 order by position""",
        photo_post_id,
    )
    assert [(row['position'], row['source_url'], row['metadata'], row['stored_object_id']) for row in photo_assets] == [
        (0, 'https://images.example.invalid/1-refresh.jpg', {'fallback_source_urls': ['https://images.example.invalid/1-refresh-fallback.jpg']}, None),
        (1, 'https://images.example.invalid/1.jpg', {'fallback_source_urls': ['https://images.example.invalid/1-fallback.jpg']}, photo_object_id),
        (2, 'https://images.example.invalid/2-refresh.jpg', {'fallback_source_urls': ['https://images.example.invalid/2-refresh-fallback.jpg']}, None),
        (3, 'https://images.example.invalid/3-refresh.jpg', {'fallback_source_urls': ['https://images.example.invalid/3-refresh-fallback.jpg']}, None),
    ]

    # Rich post refreshes share the same asset write path, so they must retain
    # attached photo metadata too while still refreshing unattached positions.
    _, photo_created = await repo.upsert_archived_post(
        owner,
        profile,
        ArchivedPost(
            platform_post_id='photo-carousel',
            original_url='https://www.tiktok.com/@creator/photo/photo-carousel',
            media_type='carousel',
            assets=[
                ArchiveMediaAsset(0, 'photo', 'https://images.example.invalid/0-rich.jpg'),
                ArchiveMediaAsset(1, 'photo', 'https://images.example.invalid/1-rich.jpg'),
                ArchiveMediaAsset(2, 'photo', 'https://images.example.invalid/2-rich.jpg'),
                ArchiveMediaAsset(3, 'photo', 'https://images.example.invalid/3-rich.jpg'),
            ],
        ),
    )
    assert photo_created is False
    refreshed_again = await database.pool.fetch(
        """select position,source_url,stored_object_id from archived_post_media_assets
           where archived_post_id=$1 order by position""",
        photo_post_id,
    )
    assert [(row['position'], row['source_url'], row['stored_object_id']) for row in refreshed_again] == [
        (0, 'https://images.example.invalid/0-rich.jpg', None),
        (1, 'https://images.example.invalid/1.jpg', photo_object_id),
        (2, 'https://images.example.invalid/2-rich.jpg', None),
        (3, 'https://images.example.invalid/3-rich.jpg', None),
    ]
    posts = await repo.list_owned_archived_profile_posts(owner, profile, limit=200)
    assert next(row for row in posts if row['platform_post_id'] == 'photo-carousel')['has_archived_media'] is True

    first, second = await asyncio.gather(
        repo.create_owned_profile_full_sync_job(owner, 1, profile, queue_name=queue.queue_name),
        repo.create_owned_profile_full_sync_job(owner, 1, profile, queue_name=queue.queue_name),
    )
    assert first[0] == second[0]
    assert sorted((first[1], second[1])) == [False, True]
    assert await repo.create_owned_profile_full_sync_job(other, 2, profile, queue_name=queue.queue_name) is None

    messages = await queue.claim(10)
    matching = [message for message in messages if message['message']['job_id'] == str(first[0])]
    assert len(matching) == 1
    await queue.archive(matching[0]['msg_id'])

    await repo.start_job(first[0])
    checkpoint = {
        'version': 1,
        'processed_post_ids': [str(index) for index in range(50)],
        'new_post_ids': [str(index) for index in range(109)],
        'posts_discovered': 109,
        'posts_added': 109,
        'posts_refreshed': 0,
        'posts_removed': 0,
        'posts_restored': 0,
        'posts_enriched': 50,
        'metadata_failures': 0,
    }
    await repo.update_profile_full_sync_checkpoint(first[0], checkpoint, 54)
    status = await repo.get_owned_profile_full_sync(owner, first[0])
    assert status['progress'] == 54
    assert status['checkpoint']['processed_post_ids'] == checkpoint['processed_post_ids']
    assert await repo.get_owned_profile_full_sync(other, first[0]) is None


async def test_canary_transaction_leaves_no_queue(database, monkeypatch):
    monkeypatch.setattr(staging_canary.settings, 'app_env', 'staging')
    before = await database.pool.fetch('select queue_name from pgmq.list_queues() order by queue_name')
    outcomes = await asyncio.gather(staging_canary.queue_roundtrip(database), staging_canary.queue_roundtrip(database))
    assert all(outcome['ok'] for outcome in outcomes), outcomes
    after = await database.pool.fetch('select queue_name from pgmq.list_queues() order by queue_name')
    assert before == after


async def test_real_worker_completes_and_archives_job(database, tmp_path):
    repo = Repository(database.pool)
    queue = JobQueue(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    job_id = await repo.create_job(owner, 0, 'test', {'roundtrip': ['real', 'worker']})
    message_id = await queue.send(str(job_id))
    # No real Telegram/provider/R2 credentials and no inherited telemetry settings.
    env = {
        'PATH': os.defpath, 'DATABASE_URL': DSN, 'APP_ENV': 'test', 'PORT': '0',
        'TELEGRAM_BOT_TOKEN': '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi',
        'QUEUE_NAME': 'media_jobs', 'PYTHONUNBUFFERED': '1',
    }
    log_path = tmp_path / 'worker.log'
    with log_path.open('wb') as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'apps.worker.main', cwd=tmp_path, env=env,
            stdout=output, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async def wait_for_archive(mid):
                for _ in range(120):
                    assert process.returncode is None, log_path.read_text()
                    archived = await database.pool.fetchval('select exists(select from pgmq.a_media_jobs where msg_id=$1)', mid)
                    if archived:
                        return await repo.get_job(job_id)
                    await asyncio.sleep(0.25)
                pytest.fail('real worker did not archive the test job: ' + log_path.read_text())

            job = await wait_for_archive(message_id)
            assert job['status'] == 'completed' and job['progress'] == 100
            assert job['input'] == {'roundtrip': ['real', 'worker']}
            assert job['result']['message'] == 'Worker test successful'
            heartbeat = await database.pool.fetchrow('select metadata from worker_heartbeats where worker_id=$1', job['result']['worker_id'])
            assert heartbeat['metadata']['pid'] == process.pid
            duplicate_id = await queue.send(str(job_id))
            duplicate = await wait_for_archive(duplicate_id)
            assert duplicate['result'] == job['result']
            assert duplicate['started_at'] == job['started_at']
            assert duplicate['completed_at'] == job['completed_at']
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
