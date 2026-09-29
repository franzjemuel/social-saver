from datetime import datetime, timezone, timedelta
from core.session_vault import SessionVault

class ProviderSessionStore:
    def __init__(self,pool,key):
        self.pool=pool; self.vault=SessionVault(key)

    async def get_or_create(self,platform,label):
        return await self.pool.fetchrow(
          """insert into provider_sessions(platform,account_label) values($1,$2)
          on conflict(platform,account_label) do update set updated_at=provider_sessions.updated_at
          returning *""",platform,label)

    async def usable(self,platform,label):
        row=await self.pool.fetchrow(
          """select * from provider_sessions where platform=$1 and account_label=$2
          and status='healthy' and (cooldown_until is null or cooldown_until<=now())""",platform,label)
        if not row or not row["encrypted_settings"]: return None
        return row,self.vault.open(row["encrypted_settings"])

    async def save_healthy(self,session_id,settings):
        sealed=self.vault.seal(settings)
        await self.pool.execute(
          """update provider_sessions set encrypted_settings=$2,status='healthy',
          last_validated_at=now(),cooldown_until=null,consecutive_failures=0,last_error_code=null,
          updated_at=now() where id=$1""",session_id,sealed)
        await self.pool.execute(
          "insert into provider_session_events(session_id,event_type) values($1,'validated')",session_id)

    async def mark(self,session_id,status,error_code=None,cooldown_minutes=None):
        await self.pool.execute(
          """update provider_sessions set status=$2,last_error_code=$3,last_error_at=now(),
          consecutive_failures=consecutive_failures+1,
          cooldown_until=case when $4::int is null then cooldown_until else now()+make_interval(mins=>$4) end,
          updated_at=now() where id=$1""",session_id,status,error_code,cooldown_minutes)
        await self.pool.execute(
          "insert into provider_session_events(session_id,event_type,detail) values($1,$2,$3)",
          session_id,status,{"error_code":error_code})
