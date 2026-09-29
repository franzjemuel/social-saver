"""Reconcile expired Live quota reservations after worker/container crashes.
Run periodically (for example every 10 minutes) from an ops scheduler.
"""
import asyncio
from core.config import settings
from core.database import Database
from core.live_quota import LiveQuota

async def main():
    db=Database(settings.database_url); await db.connect()
    quota=LiveQuota(db.pool)
    rows=await db.pool.fetch(
        """select r.id,r.live_session_id
        from live_quota_reservations r
        left join live_sessions s on s.id=r.live_session_id
        where r.status='reserved' and r.expires_at < now()
          and (s.id is null or s.status <> 'recording' or s.heartbeat_at < now()-interval '5 minutes')
        order by r.expires_at limit 100""")
    recovered=0
    for row in rows:
        actual=0
        if row["live_session_id"]:
            actual=int(await db.pool.fetchval(
                "select coalesce(sum(duration_seconds),0) from live_segments where live_session_id=$1",
                row["live_session_id"]) or 0)
            await db.pool.execute(
                """update live_sessions set status='failed',ended_at=coalesce(ended_at,now()),
                recorded_seconds=greatest(recorded_seconds,$2),stop_reason=coalesce(stop_reason,'lease_recovered'),
                error_code=coalesce(error_code,'STALE_LIVE_LEASE') where id=$1 and status in ('queued','recording')""",
                row["live_session_id"],actual)
        if await quota.settle(row["id"],actual): recovered+=1
    print({"recovered":recovered})
    await db.close()

if __name__ == "__main__": asyncio.run(main())
