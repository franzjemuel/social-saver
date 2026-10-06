class JobQueue:
    def __init__(self, pool, queue_name="media_jobs", visibility_seconds=60):
        self.pool = pool
        self.queue_name = queue_name
        self.visibility_seconds = visibility_seconds

    async def send(self, job_id: str):
        async with self.pool.acquire() as con:
            return await con.fetchval(
                "select * from pgmq.send($1, jsonb_build_object('version',1,'job_id',$2::text), 0)",
                self.queue_name, job_id
            )

    async def claim(self, qty=1):
        async with self.pool.acquire() as con:
            return await con.fetch(
                "select * from pgmq.read_with_poll($1,$2,$3,5,100)",
                self.queue_name, self.visibility_seconds, qty
            )

    async def archive(self, msg_id: int):
        async with self.pool.acquire() as con:
            return await con.fetchval(
                "select pgmq.archive($1::text,$2::bigint)", self.queue_name, msg_id
            )

    async def extend_visibility(self, msg_id: int, seconds: int):
        async with self.pool.acquire() as con:
            return await con.fetchrow(
                "select * from pgmq.set_vt($1::text,$2::bigint,$3::integer)",
                self.queue_name,
                msg_id,
                seconds,
            )

    async def send_dead_letter(self, payload: dict):
        async with self.pool.acquire() as con:
            return await con.fetchval(
                "select * from pgmq.send('dead_letter_jobs', $1::jsonb, 0)", payload
            )
