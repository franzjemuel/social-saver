import uuid

import pytest

from core.watches import WatchService


class RecordingPool:
    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))


@pytest.mark.asyncio
async def test_update_poll_normalizes_database_uuid_to_string():
    pool = RecordingPool()
    watch_id = uuid.uuid4()

    await WatchService(pool).update_poll(watch_id, {"stories": {"initialized": True}})

    assert pool.calls[0][1][0] == str(watch_id)
    assert isinstance(pool.calls[0][1][0], str)
