import asyncio, sys
from core.config import settings
from core.database import Database
from core.repository import Repository
from core.queue import JobQueue

async def main():
    if len(sys.argv)<4:
        raise SystemExit("Usage: python ops/test_live_recorder.py <app-user-uuid> <HLS-manifest-url> <minutes>")
    user_id,url,minutes=sys.argv[1],sys.argv[2],int(sys.argv[3])
    db=Database(settings.database_url); await db.connect()
    repo=Repository(db.pool); queue=JobQueue(db.pool,settings.queue_name,settings.queue_visibility_seconds)
    job_id=await repo.create_job(user_id,0,"record_live",{
      "platform":"test","target_key":"manual-hls","source_id":"manual",
      "manifest_url":url,"max_minutes":minutes})
    await queue.send(str(job_id))
    print(f"Queued bounded Live recorder test job {job_id}")
    await db.close()
if __name__=="__main__": asyncio.run(main())
