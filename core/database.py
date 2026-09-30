import json

import asyncpg


async def _init_connection(connection: asyncpg.Connection):
    """Use Python JSON values consistently on every physical pool connection."""
    for typename in ("json", "jsonb"):
        await connection.set_type_codec(
            typename, schema="pg_catalog", format="text",
            encoder=json.dumps, decoder=json.loads,
        )

class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=5, init=_init_connection,
        )

    async def close(self):
        if self.pool:
            await self.pool.close()
