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
from core.repository import Repository
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
    assert targeted_job is not None
    assert (await repo.get_job(targeted_job))['input'] == {
        'profile_id': str(profile), 'post_id': str(post),
    }

    bulk_job = await repo.create_owned_profile_media_job(owner, 1, profile)
    assert bulk_job is not None
    assert (await repo.get_job(bulk_job))['input'] == {'profile_id': str(profile)}

    assert await repo.create_owned_profile_media_job(other, 2, profile, post) is None
    assert await repo.create_owned_profile_media_job(owner, 1, profile, uuid4()) is None


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
