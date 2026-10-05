"""Real database/worker tests; opt in only with the disposable runner's DSN."""
import asyncio
from contextlib import AsyncExitStack
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
        """insert into archived_posts(archived_profile_id,platform_post_id,original_url,media_type)
           values($1,'playback-post','https://www.tiktok.com/@owner/video/playback-post','video') returning id""",
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
    assert (await repo.get_owned_archived_post_playback(owner, profile, post))['media_type'] == 'video'
    assert await repo.get_owned_archived_post_playback(other, profile, post) is None
    assert await repo.list_owned_archived_video_assets(other, profile, post) == []
    assert await repo.create_owned_profile_media_job(other, 2, profile, post) is None


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
