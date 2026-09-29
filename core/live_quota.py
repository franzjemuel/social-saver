from dataclasses import dataclass
from datetime import date, timedelta

@dataclass(frozen=True)
class LiveReservation:
    allowed: bool
    seconds: int = 0
    reservation_id: str | None = None
    reason: str | None = None

class LiveQuota:
    def __init__(self,pool): self.pool=pool

    async def reserve(self,user_id,requested_seconds,lease_seconds=10800):
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("select pg_advisory_xact_lock(hashtext($1::text))",f"live:{user_id}")
                plan=(await con.fetchval(
                    """select plan_code from subscriptions where user_id=$1 and status='active'
                    and (current_period_end is null or current_period_end>now())
                    order by current_period_end desc nulls first limit 1""",user_id)) or "free"
                limit_minutes=int(await con.fetchval(
                    "select coalesce(int_value,0) from plan_features where plan_code=$1 and feature_key='live_minutes_monthly'",
                    plan) or 0)
                if limit_minutes<=0: return LiveReservation(False,reason="LIVE_NOT_INCLUDED")
                month=date.today().replace(day=1)
                row=await con.fetchrow(
                    """insert into live_usage_monthly(user_id,month_start) values($1,$2)
                    on conflict(user_id,month_start) do update set updated_at=live_usage_monthly.updated_at
                    returning used_seconds,reserved_seconds""",user_id,month)
                available=max(0,limit_minutes*60-int(row["used_seconds"])-int(row["reserved_seconds"]))
                reserve=min(int(requested_seconds),available)
                if reserve<60: return LiveReservation(False,reason="LIVE_QUOTA_EXHAUSTED")
                await con.execute(
                    "update live_usage_monthly set reserved_seconds=reserved_seconds+$3,updated_at=now() where user_id=$1 and month_start=$2",
                    user_id,month,reserve)
                rid=await con.fetchval(
                    """insert into live_quota_reservations(user_id,month_start,reserved_seconds,expires_at)
                    values($1,$2,$3,now()+($4::text||' seconds')::interval) returning id""",
                    user_id,month,reserve,int(lease_seconds))
                return LiveReservation(True,reserve,str(rid))

    async def bind_session(self,reservation_id,session_id):
        await self.pool.execute(
            "update live_quota_reservations set live_session_id=$2 where id=$1 and status='reserved'",
            reservation_id,session_id)

    async def settle(self,reservation_id,actual_seconds):
        """Idempotently convert a reservation into usage using its original billing month."""
        async with self.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow(
                    "select * from live_quota_reservations where id=$1 for update",reservation_id)
                if not row or row["status"] != "reserved": return False
                actual=max(0,min(int(actual_seconds),int(row["reserved_seconds"])))
                await con.execute(
                    """update live_usage_monthly set reserved_seconds=greatest(0,reserved_seconds-$3),
                    used_seconds=used_seconds+$4,updated_at=now() where user_id=$1 and month_start=$2""",
                    row["user_id"],row["month_start"],row["reserved_seconds"],actual)
                await con.execute(
                    "update live_quota_reservations set status='settled',actual_seconds=$2,settled_at=now() where id=$1",
                    reservation_id,actual)
                return True

    async def release(self,reservation_id):
        """Idempotently return an unused reservation to the monthly pool."""
        async with self.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow(
                    "select * from live_quota_reservations where id=$1 for update",reservation_id)
                if not row or row["status"] != "reserved": return False
                await con.execute(
                    """update live_usage_monthly set reserved_seconds=greatest(0,reserved_seconds-$3),updated_at=now()
                    where user_id=$1 and month_start=$2""",
                    row["user_id"],row["month_start"],row["reserved_seconds"])
                await con.execute(
                    "update live_quota_reservations set status='released',actual_seconds=0,settled_at=now() where id=$1",
                    reservation_id)
                return True
