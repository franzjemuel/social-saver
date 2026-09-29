import asyncio, sys, uuid
from core.config import settings
from core.database import Database
from core.queue import JobQueue

async def main():
    db=Database(settings.database_url); await db.connect()
    q=JobQueue(db.pool,settings.queue_name,settings.queue_visibility_seconds)
    uid=await db.pool.fetchval("insert into app_users default values returning id")
    jid=await db.pool.fetchval(
      "insert into jobs(user_id,telegram_chat_id,job_type,input) values($1,0,'test','{}'::jsonb) returning id",uid)
    await q.send(str(jid))
    print(f"queued {jid}")
    for _ in range(30):
        row=await db.pool.fetchrow("select status,result,error_code,error_message from jobs where id=$1",jid)
        if row["status"] in ("completed","failed"):
            print(dict(row))
            await db.close()
            raise SystemExit(0 if row["status"]=="completed" else 1)
        await asyncio.sleep(1)
    await db.close()
    print("timeout waiting for worker",file=sys.stderr); raise SystemExit(2)

if __name__=="__main__": asyncio.run(main())
