from dataclasses import dataclass
from datetime import datetime, timezone

SUBSCRIPTION_PERIOD=2592000

@dataclass(frozen=True)
class Offer:
    code:str
    name:str
    stars:int

class PaymentService:
    def __init__(self,pool): self.pool=pool

    async def offers(self):
        rows=await self.pool.fetch("select code,name,stars_price from plans where is_active=true and code<>'free' and stars_price is not null order by stars_price")
        return [Offer(r["code"],r["name"],int(r["stars_price"])) for r in rows]

    async def offer(self,code):
        r=await self.pool.fetchrow("select code,name,stars_price from plans where code=$1 and is_active=true and stars_price is not null",code)
        return Offer(r["code"],r["name"],int(r["stars_price"])) if r else None

    def payload(self,user_id,plan_code): return f"sub:v1:{user_id}:{plan_code}"

    def parse_payload(self,payload):
        p=payload.split(":")
        if len(p)!=4 or p[:2]!=["sub","v1"]: raise ValueError("Invalid invoice payload")
        return p[2],p[3]

    async def activate(self,user_id,plan_code,payment):
        charge=payment.telegram_payment_charge_id
        expires=datetime.fromtimestamp(payment.subscription_expiration_date,tz=timezone.utc) if payment.subscription_expiration_date else None
        async with self.pool.acquire() as con:
            async with con.transaction():
                event=await con.fetchval(
                  """insert into payment_events(telegram_payment_charge_id,user_id,plan_code,invoice_payload,currency,total_amount,
                  is_recurring,is_first_recurring,subscription_expiration_date,raw)
                  values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                  on conflict(telegram_payment_charge_id) do nothing returning id""",
                  charge,user_id,plan_code,payment.invoice_payload,payment.currency,payment.total_amount,
                  bool(payment.is_recurring),bool(payment.is_first_recurring),expires,{})
                if not event: return False

                existing=await con.fetchrow(
                  """select * from subscriptions where user_id=$1 and provider='telegram_stars'
                  and plan_code=$2 and status='active' order by created_at desc limit 1""",user_id,plan_code)

                if existing and payment.is_recurring and not payment.is_first_recurring:
                    await con.execute(
                      """update subscriptions set provider_charge_id=$2,current_period_end=$3,
                      cancel_at_period_end=false,updated_at=now() where id=$1""",
                      existing["id"],charge,expires)
                    sub_id=existing["id"]
                    action="renewed"
                else:
                    # New purchase/plan switch. Local entitlement changes immediately.
                    # Caller must separately cancel old Telegram auto-renewals before a plan switch UI is considered complete.
                    await con.execute(
                      "update subscriptions set status='canceled',updated_at=now() where user_id=$1 and status='active'",user_id)
                    sub_id=await con.fetchval(
                      """insert into subscriptions(user_id,plan_code,provider,provider_charge_id,initial_charge_id,status,
                      current_period_start,current_period_end)
                      values($1,$2,'telegram_stars',$3,$3,'active',now(),$4) returning id""",
                      user_id,plan_code,charge,expires)
                    action="activated"

                await con.execute(
                  "insert into subscription_audit(user_id,subscription_id,action,metadata) values($1,$2,$3,$4)",
                  user_id,sub_id,action,{"charge_id":charge,"plan":plan_code})
                return True

    async def active_subscription(self,user_id):
        return await self.pool.fetchrow(
          """select * from subscriptions where user_id=$1 and status='active'
          and (current_period_end is null or current_period_end>now())
          order by current_period_end desc nulls first limit 1""",user_id)

    async def mark_cancel_at_period_end(self,user_id,subscription_id):
        await self.pool.execute(
          """update subscriptions set cancel_at_period_end=true,canceled_at=now(),updated_at=now()
          where id=$1 and user_id=$2 and status='active'""",subscription_id,user_id)
        await self.pool.execute(
          "insert into subscription_audit(user_id,subscription_id,action) values($1,$2,'cancel_at_period_end')",
          user_id,subscription_id)

    async def expire_due(self):
        return await self.pool.fetch(
          """update subscriptions set status='expired',updated_at=now()
          where status='active' and current_period_end is not null and current_period_end<=now()
          returning id,user_id,plan_code""")
