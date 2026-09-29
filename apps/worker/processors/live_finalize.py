from core.config import settings
from core.storage import R2Storage
from core.live_finalizer import LiveFinalizer

async def process_finalize_live(job,db,bot):
    data=job["input"] or {}; user_id=job["user_id"]
    storage=R2Storage(settings.r2_account_id,settings.r2_access_key_id,
                      settings.r2_secret_access_key,settings.r2_bucket,settings.r2_presign_seconds)
    result=await LiveFinalizer(db.pool,storage).finalize(data["live_session_id"],user_id)
    return result
