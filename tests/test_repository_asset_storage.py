import pytest

from core.repository import Repository


class RecordingPool:
    def __init__(self):
        self.query = None
        self.args = None

    async def execute(self, query, *args):
        self.query = query
        self.args = args


@pytest.mark.asyncio
async def test_update_asset_storage_types_nullable_provider_parameter():
    pool = RecordingPool()

    await Repository(pool).update_asset_storage(
        "media-id", 0, size_bytes=42, sha256="abc",
        storage_provider=None, storage_key=None,
    )

    assert "$5::text" in pool.query
    assert pool.args[4] is None
