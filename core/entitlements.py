from dataclasses import dataclass

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None
    limit: int = 0
    used: int = 0
    reserved: int = 0

class EntitlementService:
    def __init__(self,pool): self.pool=pool

    async def plan_for(self,user_id):
        return (await self.pool.fetchval(
          """select plan_code from subscriptions where user_id=$1 and status='active'
          and (current_period_end is null or current_period_end>now())
          order by current_period_end desc nulls first limit 1""",user_id)) or "free"

    async def int_feature(self,user_id,key):
        plan=await self.plan_for(user_id)
        value=await self.pool.fetchval(
          "select coalesce(int_value,0) from plan_features where plan_code=$1 and feature_key=$2",plan,key)
        return plan,int(value or 0)

    async def archive_usage(self,user_id):
        return int(await self.pool.fetchval(
          """select coalesce(sum(so.size_bytes),0) from archive_entries ae
          join archive_entry_assets aea on aea.archive_entry_id=ae.id
          join stored_objects so on so.id=aea.stored_object_id
          where ae.user_id=$1 and ae.deleted_at is null and so.deleted_at is null""",user_id) or 0)

    async def authorize_archive(self,user_id,estimated_bytes=0):
        _,limit=await self.int_feature(user_id,"archive_bytes")
        used=await self.archive_usage(user_id)
        reserved=int(await self.pool.fetchval(
          """select coalesce(sum(amount),0) from quota_reservations where user_id=$1
          and resource_key='archive_bytes' and status='reserved' and expires_at>now()""",user_id) or 0)
        if limit<=0: return Decision(False,"ARCHIVE_NOT_INCLUDED",limit,used,reserved)
        if used+reserved+estimated_bytes>limit:
            return Decision(False,"ARCHIVE_QUOTA_EXCEEDED",limit,used,reserved)
        return Decision(True,None,limit,used,reserved)

    async def reserve_archive(self,user_id,job_id,amount,ttl_minutes=30):
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("select pg_advisory_xact_lock(hashtext($1::text))",str(user_id))
                plan=(await con.fetchval(
                  """select plan_code from subscriptions where user_id=$1 and status='active'
                  and (current_period_end is null or current_period_end>now())
                  order by current_period_end desc nulls first limit 1""",user_id)) or "free"
                limit=int(await con.fetchval(
                  "select coalesce(int_value,0) from plan_features where plan_code=$1 and feature_key='archive_bytes'",plan) or 0)
                used=int(await con.fetchval(
                  """select coalesce(sum(so.size_bytes),0) from archive_entries ae
                  join archive_entry_assets aea on aea.archive_entry_id=ae.id
                  join stored_objects so on so.id=aea.stored_object_id
                  where ae.user_id=$1 and ae.deleted_at is null and so.deleted_at is null""",user_id) or 0)
                reserved=int(await con.fetchval(
                  """select coalesce(sum(amount),0) from quota_reservations where user_id=$1
                  and resource_key='archive_bytes' and status='reserved' and expires_at>now()""",user_id) or 0)
                if limit<=0 or used+reserved+amount>limit: return None
                return await con.fetchval(
                  """insert into quota_reservations(user_id,resource_key,amount,job_id,expires_at)
                  values($1,'archive_bytes',$2,$3,now()+make_interval(mins=>$4)) returning id""",
                  user_id,amount,job_id,ttl_minutes)

    async def settle(self,reservation_id,actual_amount):
        await self.pool.execute(
          "update quota_reservations set status='settled',actual_amount=$2 where id=$1 and status='reserved'",
          reservation_id,actual_amount)

    async def release(self,reservation_id):
        await self.pool.execute(
          "update quota_reservations set status='released' where id=$1 and status='reserved'",reservation_id)
