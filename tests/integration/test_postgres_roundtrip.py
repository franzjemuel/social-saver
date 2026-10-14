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
from apps.worker.processors.profile_highlight_sync import (
    TikTokProfileHighlightSyncFailure,
    process_sync_profile_highlights,
)
from providers.base import (
    ArchiveMediaAsset,
    ArchivedHighlightCollectionSnapshot,
    ArchivedHighlightStoryMembership,
    ArchivedPost,
    HighlightDiscoveryResult,
    HighlightStoryMediaDescriptor,
)
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

    await repo.start_job(first.id)
    assert await repo.claim_profile_photo_delivery_attempt(first.id, owner, profile, post) == "claimed"
    assert await repo.confirm_profile_photo_delivery_attempt(first.id, owner, profile, post) is True
    assert await repo.claim_profile_photo_delivery_attempt(first.id, owner, profile, post) == "confirmed"
    await repo.complete_job(first.id, {"sent": 2})

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


async def test_highlight_story_media_assets_are_tenant_scoped_ordered_and_idempotent(database):
    """Migration 025: no descriptors persist, storage attachment is owner-only."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','highlight-media-owner','owner') returning id""", owner,
    )
    collection = await database.pool.fetchval(
        """insert into archived_highlight_collections
               (archived_profile_id,provider_namespace,provider_collection_id)
           values($1,'synthetic','collection') returning id""", profile,
    )
    membership = await database.pool.fetchval(
        """insert into archived_highlight_story_memberships
               (archived_highlight_collection_id,provider_namespace,provider_item_id,provider_position,media_type)
           values($1,'synthetic','story',0,'photo') returning id""", collection,
    )
    descriptors = (
        HighlightStoryMediaDescriptor(0, 'photo', ('https://never-persist.invalid/a',)),
        HighlightStoryMediaDescriptor(1, 'photo', ('https://never-persist.invalid/b',)),
    )
    first = await repo.upsert_owned_highlight_story_media_assets(owner, profile, membership, descriptors)
    assert first == {'inserted': 2, 'refreshed': 0}
    assert await repo.upsert_owned_highlight_story_media_assets(other, profile, membership, descriptors) is None
    pending = await repo.list_owned_highlight_story_media_assets(owner, profile, membership)
    assert [row['position'] for row in pending] == [0, 1]
    assert await repo.list_owned_highlight_story_media_assets(other, profile, membership) == []
    digest = hashlib.sha256(b'highlight-story-media-object').hexdigest()
    object_id = await database.pool.fetchval(
        """insert into stored_objects(sha256,storage_key,size_bytes,content_type)
           values($1,'private-highlight-media',1,'image/jpeg') returning id""", digest,
    )
    assert await repo.attach_owned_highlight_story_media_object(owner, profile, pending[0]['id'], object_id)
    # Reordered metadata does not mutate an attached asset's durable identity.
    refreshed = await repo.upsert_owned_highlight_story_media_assets(
        owner, profile, membership,
        (HighlightStoryMediaDescriptor(0, 'video', ('https://never-persist.invalid/changed',)),),
    )
    assert refreshed == {'inserted': 0, 'refreshed': 0}
    playback = await repo.get_owned_highlight_story_media_playback(owner, profile, membership, 0)
    assert playback['storage_key'] == 'private-highlight-media'
    assert await repo.get_owned_highlight_story_media_playback(other, profile, membership, 0) is None
    assert await database.pool.fetchval(
        "select count(*) from archived_highlight_story_media_assets where archived_highlight_story_membership_id=$1",
        membership,
    ) == 2


async def test_highlight_provider_invocation_budget_is_tenant_safe_and_nonreusable(database):
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','ledger-account','owner') returning id""", owner,
    )
    job = await database.pool.fetchval(
        """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
           values($1,1,'sync_profile_highlights',jsonb_build_object('profile_id',$2::uuid),'mini_app')
           returning id""", owner, profile,
    )
    assert await repo.initialize_owned_highlight_provider_budget(owner, job, profile, 650_000)
    assert await repo.initialize_owned_highlight_provider_budget(other, job, profile, 650_000) is None
    discovery = await repo.reserve_owned_highlight_provider_invocation(
        owner, job, profile, provider_namespace='synthetic', operation='collection_list', maximum_micro_usd=100_000,
    )
    enumeration = await repo.reserve_owned_highlight_provider_invocation(
        owner, job, profile, provider_namespace='synthetic', operation='collection_items', maximum_micro_usd=550_000,
    )
    assert discovery['sequence'] == 0 and enumeration['sequence'] == 1
    assert await repo.reserve_owned_highlight_provider_invocation(
        owner, job, profile, provider_namespace='synthetic', operation='collection_items', maximum_micro_usd=1,
    ) is None
    claimed = await repo.claim_owned_highlight_provider_invocation(owner, job, discovery['id'])
    assert claimed and claimed['status'] == 'started'
    assert await repo.claim_owned_highlight_provider_invocation(owner, job, discovery['id']) is None
    indeterminate = await repo.finish_owned_highlight_provider_invocation(
        owner, job, discovery['id'], status='indeterminate', error_code='timeout_ambiguous',
    )
    assert indeterminate['status'] == 'indeterminate'
    assert await repo.finish_owned_highlight_provider_invocation(
        owner, job, discovery['id'], status='succeeded', receipt='later',
    ) is None


async def test_highlight_story_media_job_can_use_same_tenant_owned_invocation_ledger(database):
    """The single-Story worker uses migration 026 without a second ledger."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','ledger-media-account','owner') returning id""", owner,
    )
    job = await database.pool.fetchval(
        """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
           values($1,1,'archive_highlight_story_media',
                  jsonb_build_object('profile_id',$2::uuid,'membership_id',gen_random_uuid()),'mini_app')
           returning id""", owner, profile,
    )
    assert await repo.initialize_owned_highlight_provider_budget(owner, job, profile, 10)
    assert await repo.initialize_owned_highlight_provider_budget(other, job, profile, 10) is None
    invocation = await repo.reserve_owned_highlight_provider_invocation(
        owner, job, profile, provider_namespace='synthetic', operation='collection_items', maximum_micro_usd=10,
    )
    assert invocation
    assert await repo.claim_owned_highlight_provider_invocation(owner, job, invocation['id'])
    done = await repo.finish_owned_highlight_provider_invocation(
        owner, job, invocation['id'], status='succeeded', error_code=None,
    )
    assert done['status'] == 'succeeded'


async def test_profile_highlight_sync_jobs_are_tenant_safe_atomic_and_coalesced(database):
    """Exercise the new job SQL, ownership joins, PGMQ and execution latch."""
    repo = Repository(database.pool)
    queue = JobQueue(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','highlight-sync-owner','owner') returning id""", owner,
    )
    other_profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','highlight-sync-other','other') returning id""", other,
    )

    concurrent = await asyncio.gather(*(
        repo.create_owned_profile_highlight_sync_job(owner, 1, profile, queue_name=queue.queue_name)
        for _ in range(8)
    ))
    first = concurrent[0]
    assert {item.id for item in concurrent} == {first.id}
    assert sum(item.created for item in concurrent) == 1
    assert await repo.create_owned_profile_highlight_sync_job(other, 2, profile, queue_name=queue.queue_name) is None
    separate = await repo.create_owned_profile_highlight_sync_job(other, 2, other_profile, queue_name=queue.queue_name)
    assert separate.id != first.id
    assert await repo.get_owned_profile_highlight_sync(other, first.id) is None
    assert await repo.get_owned_profile_highlight_sync(owner, first.id) is not None

    messages = await queue.claim(10)
    matching = [message for message in messages if message['message']['job_id'] in {str(first.id), str(separate.id)}]
    assert len(matching) == 2  # coalescing did not emit a second queue message
    for message in matching:
        await queue.archive(message['msg_id'])

    assert await repo.claim_profile_highlight_sync_execution(first.id, owner, profile) is True
    # A redelivered PGMQ message observes the durable latch and must not trigger
    # a second future provider invocation.
    assert await repo.claim_profile_highlight_sync_execution(first.id, owner, profile) is False
    assert await repo.claim_profile_highlight_sync_execution(first.id, other, profile) is False

    rollback_profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','highlight-sync-rollback','rollback') returning id""", owner,
    )
    with pytest.raises(asyncpg.PostgresError):
        await repo.create_owned_profile_highlight_sync_job(
            owner, 1, rollback_profile, queue_name="queue_that_does_not_exist",
        )
    assert await database.pool.fetchval(
        """select count(*) from jobs
           where user_id=$1 and job_type='sync_profile_highlights'
             and input->>'profile_id'=$2::uuid::text""",
        owner, rollback_profile,
    ) == 0


async def test_profile_highlight_sync_persists_safe_checkpoint_after_later_collection_database_failure(database):
    """The first committed collection remains observable when a later one fails."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval(
        """insert into archived_profiles(user_id,platform,platform_account_id,username)
           values($1,'tiktok','highlight-sync-checkpoint','owner') returning id""", owner,
    )
    job_id = await repo.create_job(
        owner, 1, 'sync_profile_highlights', {'profile_id': str(profile)}, source_channel='mini_app',
    )
    await repo.start_job(job_id)

    class Scanner:
        class Profile:
            username = 'owner'
            user_id = 'highlight-sync-checkpoint'

        def resolve_profile(self, _username):
            return self.Profile()

    class Provider:
        async def discover_highlights(self, _username):
            return HighlightDiscoveryResult((
                _highlight_snapshot('first', (_highlight_membership('first-item', 0),)),
                _highlight_snapshot('fail', (_highlight_membership('second-item', 0),)),
            ))

    await database.pool.execute("""
        create function fail_one_highlight_collection() returns trigger language plpgsql as $$
        begin
          if new.provider_collection_id = 'fail' then raise exception 'synthetic later collection failure'; end if;
          return new;
        end; $$;
        create trigger fail_one_highlight_collection
        before insert on archived_highlight_collections
        for each row execute function fail_one_highlight_collection();
    """)
    try:
        with pytest.raises(TikTokProfileHighlightSyncFailure) as exc:
            await process_sync_profile_highlights(
                {'id': job_id, 'user_id': owner, 'input': {'profile_id': str(profile)}},
                repo, Provider(), Scanner(), True,
            )
        assert exc.value.code == 'highlights_reconciliation_failed'
        checkpoint = await repo.get_owned_profile_highlight_sync(owner, job_id)
        assert checkpoint['checkpoint']['outcome'] == 'partial_failure'
        assert checkpoint['checkpoint']['collections_created'] == 1
        assert checkpoint['checkpoint']['memberships_created'] == 1
        assert await database.pool.fetchval(
            "select count(*) from archived_highlight_collections where archived_profile_id=$1", profile,
        ) == 1
    finally:
        await database.pool.execute('drop trigger fail_one_highlight_collection on archived_highlight_collections')
        await database.pool.execute('drop function fail_one_highlight_collection()')


def _highlight_membership(item_id, position, *, namespace='apify:tiktok-highlights'):
    return ArchivedHighlightStoryMembership(
        provider_namespace=namespace,
        provider_item_id=item_id,
        provider_position=position,
        media_type='video',
        caption=f'caption-{item_id}',
        has_media_descriptor=True,
        has_cover_descriptor=True,
    )


_UNSET = object()


def _highlight_snapshot(
    collection_id, memberships=(), *, complete=True, reported_count=_UNSET, upstream_verified=False,
):
    return ArchivedHighlightCollectionSnapshot(
        provider_namespace='apify:tiktok-highlights',
        provider_collection_id=collection_id,
        title=f'collection-{collection_id}',
        reported_item_count=len(memberships) if reported_count is _UNSET else reported_count,
        has_cover_descriptor=True,
        observation_complete=complete,
        memberships=tuple(memberships),
        upstream_completeness_verified=upstream_verified,
    )


async def test_highlight_snapshots_are_tenant_safe_idempotent_and_non_destructive(database):
    """Exercise the new collection/member uniqueness and observed-order contract."""
    from core.repository import HighlightSnapshotValidationError

    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval("""insert into archived_profiles
        (user_id,platform,platform_account_id,username)
        values($1,'tiktok','highlight-owner','owner') returning id""", owner)
    other_profile = await database.pool.fetchval("""insert into archived_profiles
        (user_id,platform,platform_account_id,username)
        values($1,'tiktok','highlight-other','other') returning id""", other)

    initial = _highlight_snapshot('collection-a', (
        _highlight_membership('shared-item', 0),
        _highlight_membership('second-item', 1),
    ), upstream_verified=True)
    first = await repo.reconcile_owned_highlight_collection_snapshot(owner, profile, initial)
    assert first.collection_created is True
    assert (first.memberships_created, first.memberships_refreshed) == (2, 0)
    assert first.snapshot_complete is True
    second = await repo.reconcile_owned_highlight_collection_snapshot(owner, profile, initial)
    assert second.collection_created is False
    assert (second.memberships_created, second.memberships_refreshed) == (0, 2)

    collections = await repo.list_owned_archived_highlight_collections(owner, profile)
    assert len(collections) == 1
    collection_id = collections[0]['id']
    assert collections[0]['last_snapshot_complete'] is True
    assert collections[0]['last_snapshot_count_consistent'] is True
    assert collections[0]['last_snapshot_provider_response_complete'] is True
    assert collections[0]['last_snapshot_upstream_verified'] is True
    assert {'source_url', 'storage_key', 'provider_response'} & set(collections[0]) == set()
    assert await repo.list_owned_archived_highlight_collections(other, other_profile) == []
    assert await repo.list_owned_archived_highlight_collections(other, profile) is None
    assert await repo.list_owned_archived_highlight_collections(owner, uuid4()) is None
    assert await repo.list_owned_archived_highlight_collections(owner, profile, offset=99) == []
    assert await repo.list_owned_archived_highlight_memberships(other, profile, collection_id) is None
    assert await repo.list_owned_archived_highlight_memberships(owner, profile, uuid4()) is None
    assert await repo.list_owned_archived_highlight_memberships(owner, profile, collection_id, offset=99) == []
    with pytest.raises(PermissionError):
        await repo.reconcile_owned_highlight_collection_snapshot(other, profile, initial)

    reordered_partial = _highlight_snapshot(
        'collection-a',
        (_highlight_membership('second-item', 0),),
        complete=False,
        reported_count=2,
    )
    await repo.reconcile_owned_highlight_collection_snapshot(owner, profile, reordered_partial)
    memberships = await repo.list_owned_archived_highlight_memberships(owner, profile, collection_id)
    positions = {row['provider_item_id']: row['provider_position'] for row in memberships}
    assert positions == {'second-item': 0, 'shared-item': 0}
    assert all(row['is_present_on_original'] for row in memberships)
    assert {'source_url', 'storage_key', 'provider_response'} & set(memberships[0]) == set()
    refreshed_collection = (await repo.list_owned_archived_highlight_collections(owner, profile))[0]
    assert refreshed_collection['last_snapshot_complete'] is False
    assert refreshed_collection['last_snapshot_count_consistent'] is False
    assert refreshed_collection['last_absent_at'] is None
    latest_snapshot_id = refreshed_collection['latest_snapshot_id']
    observed_snapshot_ids = {row['provider_item_id']: row['last_observed_snapshot_id'] for row in memberships}
    assert observed_snapshot_ids['second-item'] == latest_snapshot_id
    assert observed_snapshot_ids['shared-item'] != latest_snapshot_id
    latest_observed = {row['provider_item_id']: row['observed_in_latest_snapshot'] for row in memberships}
    assert latest_observed == {'second-item': True, 'shared-item': False}

    # A matching count is structural consistency, not independently verified
    # upstream completeness. Neither response evidence alone is sufficient.
    unverified = await repo.reconcile_owned_highlight_collection_snapshot(
        owner,
        profile,
        _highlight_snapshot('unverified', (_highlight_membership('item', 0),), upstream_verified=False),
    )
    assert unverified.snapshot_complete is False
    inconsistent = await repo.reconcile_owned_highlight_collection_snapshot(
        owner,
        profile,
        _highlight_snapshot(
            'inconsistent', (_highlight_membership('item', 0),), reported_count=2, upstream_verified=True,
        ),
    )
    assert inconsistent.reported_count_consistent is False
    assert inconsistent.snapshot_complete is False
    completeness = {
        row['provider_collection_id']: row
        for row in await repo.list_owned_archived_highlight_collections(owner, profile)
    }
    assert completeness['unverified']['last_snapshot_count_consistent'] is True
    assert completeness['unverified']['last_snapshot_provider_response_complete'] is True
    assert completeness['unverified']['last_snapshot_upstream_verified'] is False
    assert completeness['unverified']['last_snapshot_complete'] is False
    assert completeness['inconsistent']['last_snapshot_count_consistent'] is False
    assert completeness['inconsistent']['last_snapshot_complete'] is False

    unknown_count = await repo.reconcile_owned_highlight_collection_snapshot(
        owner,
        profile,
        _highlight_snapshot(
            'unknown-count', (_highlight_membership('item', 0),), reported_count=None, upstream_verified=True,
        ),
    )
    assert unknown_count.reported_count_consistent is False
    assert unknown_count.snapshot_complete is False

    empty_complete = await repo.reconcile_owned_highlight_collection_snapshot(
        owner,
        profile,
        _highlight_snapshot('empty-complete', (), reported_count=0, upstream_verified=True),
    )
    assert empty_complete.reported_count_consistent is True
    assert empty_complete.snapshot_complete is True

    # An item may be in two collections without duplication inside either one.
    second_collection = await repo.reconcile_owned_highlight_collection_snapshot(
        owner,
        profile,
        _highlight_snapshot('collection-b', (_highlight_membership('shared-item', 0),)),
    )
    assert second_collection.memberships_created == 1
    assert await database.pool.fetchval(
        "select count(*) from archived_highlight_story_memberships where provider_item_id='shared-item'",
    ) == 2
    assert await repo.reconcile_owned_highlight_collection_snapshot(
        other,
        other_profile,
        _highlight_snapshot('collection-a', (_highlight_membership('shared-item', 0),)),
    )

    malformed = _highlight_snapshot('malformed', (
        _highlight_membership('duplicate', 0), _highlight_membership('duplicate', 1),
    ))
    before = await database.pool.fetchval('select count(*) from archived_highlight_collections')
    with pytest.raises(HighlightSnapshotValidationError):
        await repo.reconcile_owned_highlight_collection_snapshot(owner, profile, malformed)
    assert await database.pool.fetchval('select count(*) from archived_highlight_collections') == before

    empty = await repo.reconcile_owned_highlight_collection_snapshot(
        owner, profile, _highlight_snapshot('empty', (), complete=True, reported_count=0),
    )
    assert empty.memberships_created == 0


async def test_highlight_snapshot_database_failure_rolls_back_collection_and_memberships(database):
    """Validation happens before the transaction; an in-transaction failure rolls it back."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    profile = await database.pool.fetchval("""insert into archived_profiles
        (user_id,platform,platform_account_id,username)
        values($1,'tiktok','highlight-rollback','owner') returning id""", owner)
    await database.pool.execute("""
        create function fail_highlight_membership_insert() returns trigger language plpgsql as $$
        begin raise exception 'synthetic membership insert failure'; end; $$;
        create trigger fail_highlight_membership_insert
        before insert on archived_highlight_story_memberships
        for each row execute function fail_highlight_membership_insert();
    """)
    try:
        with pytest.raises(asyncpg.RaiseError):
            await repo.reconcile_owned_highlight_collection_snapshot(
                owner,
                profile,
                _highlight_snapshot('rollback', (_highlight_membership('item', 0),)),
            )
        assert await database.pool.fetchval(
            "select count(*) from archived_highlight_collections where archived_profile_id=$1",
            profile,
        ) == 0
    finally:
        await database.pool.execute('drop trigger fail_highlight_membership_insert on archived_highlight_story_memberships')
        await database.pool.execute('drop function fail_highlight_membership_insert()')


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


async def test_tiktok_quick_save_delivery_claim_is_durable_and_tenant_scoped(database):
    """Concurrent redeliveries cannot claim a second Telegram upload."""
    repo = Repository(database.pool)
    owner = await database.pool.fetchval('insert into app_users default values returning id')
    other = await database.pool.fetchval('insert into app_users default values returning id')
    job_id = await repo.create_job(owner, 12345, 'tiktok_quick_save', {
        'url': 'https://www.tiktok.com/@creator/video/7176363825556376859',
    })
    await repo.start_job(job_id)
    claims = await asyncio.gather(*(
        repo.claim_tiktok_quick_save_delivery(job_id, owner) for _ in range(8)
    ))
    assert claims.count(True) == 1
    assert claims.count(False) == 7
    assert await repo.claim_tiktok_quick_save_delivery(job_id, other) is False
    row = await repo.get_job(job_id)
    assert row['input']['telegram_delivery_claimed'] is True
    assert row['input']['url'] == 'https://www.tiktok.com/@creator/video/7176363825556376859'
    assert await repo.claim_tiktok_quick_save_delivery(job_id, owner) is False
