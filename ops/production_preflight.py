import asyncio, shutil
from core.config import settings
from core.database import Database

REQUIRED={
 "telegram_bot_token":settings.telegram_bot_token,"database_url":settings.database_url,
 "r2_account_id":settings.r2_account_id,"r2_access_key_id":settings.r2_access_key_id,
 "r2_secret_access_key":settings.r2_secret_access_key,"r2_bucket":settings.r2_bucket,
}
async def main():
    errors=[]; missing=[k for k,v in REQUIRED.items() if not v]
    print("Required settings:", "ok" if not missing else "MISSING: "+", ".join(missing))
    if missing: errors.append("required settings")
    print("FFmpeg:", shutil.which("ffmpeg") or "MISSING")
    if not shutil.which("ffmpeg"): errors.append("ffmpeg")
    if not missing:
        db=Database(settings.database_url)
        try:
            await db.connect()
            pgmq=await db.pool.fetchval("select exists(select 1 from pg_extension where extname='pgmq')")
            migrations=await db.pool.fetchval("select count(*) from supabase_migrations.schema_migrations")
            print("Database: connected"); print("PGMQ:", "ok" if pgmq else "MISSING")
            print("Applied migrations:",migrations)
            if not pgmq: errors.append("pgmq")
            await db.close()
        except Exception as exc:
            print("Database: FAILED",type(exc).__name__); errors.append("database")
    if errors: raise SystemExit("PRE-FLIGHT FAILED: "+", ".join(errors))
    print("PRE-FLIGHT PASSED")
if __name__=="__main__": asyncio.run(main())
