import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from ops import staging_canary as canary


@pytest.fixture(autouse=True)
def staging(monkeypatch):
    monkeypatch.setattr(canary.settings, 'app_env', 'staging')


def database_probe(failure=None, string_body=False, archive=True):
    transaction = SimpleNamespace(start=AsyncMock(), rollback=AsyncMock())
    con = SimpleNamespace(execute=AsyncMock(), fetchval=AsyncMock(), fetch=AsyncMock(),
                          transaction=Mock(return_value=transaction))
    messages = []

    async def fetchval(sql, *args):
        if 'pgmq.send' in sql:
            body = args[1] if string_body else json.loads(args[1])
            messages.append({'msg_id': 42, 'message': body})
            return 42
        return archive

    async def fetch(*args):
        if failure:
            raise failure
        return messages

    con.fetchval.side_effect = fetchval
    con.fetch.side_effect = fetch
    acquire = AsyncMock()
    acquire.__aenter__.return_value = con
    pool = SimpleNamespace(acquire=Mock(return_value=acquire))
    return SimpleNamespace(pool=pool), con, transaction


@pytest.mark.parametrize('string_body', [False, True])
async def test_queue_probe_isolated_and_rolled_back(string_body):
    db, con, transaction = database_probe(string_body=string_body)
    outcome = await canary.queue_roundtrip(db)
    assert outcome['ok']
    queue = con.execute.call_args.args[1]
    assert queue.startswith('canary_') and len(queue) <= 47
    assert queue != canary.settings.queue_name
    assert con.fetch.call_args.args[1] == queue
    assert all(c.args[1] == queue for c in con.fetchval.call_args_list)
    transaction.rollback.assert_awaited_once()


@pytest.mark.parametrize('failure', [RuntimeError('secret URL must not leak'), asyncio.CancelledError()])
async def test_queue_failure_always_rolls_back(failure):
    db, con, transaction = database_probe(failure=failure)
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await canary.queue_roundtrip(db)
    else:
        outcome = await canary.queue_roundtrip(db)
        assert not outcome['ok']
        assert outcome['detail'] == 'RuntimeError'
    transaction.rollback.assert_awaited_once()


async def test_archive_failure_is_not_reported_as_success():
    db, _, transaction = database_probe(archive=False)
    assert not (await canary.queue_roundtrip(db))['ok']
    transaction.rollback.assert_awaited_once()


async def test_concurrent_queue_probes_have_distinct_names():
    probes = [database_probe(), database_probe()]
    await asyncio.gather(*(canary.queue_roundtrip(p[0]) for p in probes))
    assert probes[0][1].execute.call_args.args[1] != probes[1][1].execute.call_args.args[1]


@pytest.mark.parametrize('stage', ['put_file', 'download_file', 'delete', None])
async def test_r2_cleanup_on_failure_and_success(monkeypatch, stage):
    storage = SimpleNamespace(put_file=AsyncMock(), download_file=AsyncMock(), delete=AsyncMock())

    async def download(key, path):
        path.write_text('social-saver-canary')

    storage.download_file.side_effect = download
    if stage:
        getattr(storage, stage).side_effect = RuntimeError('sensitive exception')
    monkeypatch.setattr(canary, 'R2Storage', Mock(return_value=storage))
    outcome = await canary.r2_roundtrip()
    assert outcome['ok'] is (stage is None)
    storage.delete.assert_awaited_once()
    assert storage.delete.call_args.args[0] == storage.put_file.call_args.args[1]
    assert 'sensitive' not in outcome['detail']
    if stage == 'delete':
        assert outcome['detail'].startswith('cleanup_failed:')


async def test_production_refused_before_network(monkeypatch):
    monkeypatch.setattr(canary.settings, 'app_env', 'production')
    telegram = AsyncMock()
    monkeypatch.setattr(canary, 'telegram_check', telegram)
    monkeypatch.setattr(sys, 'argv', ['staging_canary'])
    with pytest.raises(SystemExit) as exc:
        await canary.main()
    assert exc.value.code == 2
    telegram.assert_not_awaited()
    assert not (await canary.queue_roundtrip(None))['ok']
    assert not (await canary.r2_roundtrip())['ok']
