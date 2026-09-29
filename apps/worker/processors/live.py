from core.config import settings
from core.live_quota import LiveQuota
from core.live_recorder import LiveRecorder
from core.storage import R2Storage
from providers.live import LiveSource
from core.live_source_security import validate_provider_source, UnsafeLiveSource
from providers.instagram.live_probe import InstagramLiveProbe

async def process_record_live(job,db):
    """Record one Live job with crash-recoverable quota accounting.

    The global advisory lock is acquired *before* quota is reserved, so a duplicate
    delivery cannot consume a second reservation while another recorder is active.
    job_id uniquely owns a live_session, making retries idempotent at the DB layer.
    """
    data=job["input"] or {}
    user_id=job["user_id"]
    quota=LiveQuota(db.pool)
    requested=min(int(data.get("max_minutes",30))*60,settings.live_hard_max_minutes*60)

    con=await db.pool.acquire()
    got=await con.fetchval("select pg_try_advisory_lock(hashtext('social-saver:live-recorder'))")
    if not got:
        await db.pool.release(con)
        return {"skipped":True,"reason":"LIVE_RECORDER_BUSY"}

    reservation=None
    session_id=None
    try:
        # A completed retry returns the already-created result instead of recording twice.
        existing=await con.fetchrow(
            "select id,status,recorded_seconds,bytes_uploaded from live_sessions where job_id=$1",job["id"])
        if existing and existing["status"] in ("completed","stopped"):
            return {"live_session_id":str(existing["id"]),"status":existing["status"],
                    "seconds":existing["recorded_seconds"],"bytes":existing["bytes_uploaded"],"replayed":True}
        if existing and existing["status"] == "recording":
            # The global lock means this can only be stale state from an interrupted attempt.
            await con.execute(
                "update live_sessions set status='failed',ended_at=now(),stop_reason='retry_after_interruption',error_code='WORKER_INTERRUPTED' where id=$1",
                existing["id"])

        reservation=await quota.reserve(user_id,requested,settings.live_hard_max_minutes*60+900)
        if not reservation.allowed:
            return {"skipped":True,"reason":reservation.reason}

        session_id=await con.fetchval(
          """insert into live_sessions(job_id,user_id,platform,target_key,source_id,max_minutes)
          values($1,$2,$3,$4,$5,$6)
          on conflict (job_id) where job_id is not null do update set status='queued', heartbeat_at=null,
            ended_at=null, stop_reason=null, error_code=null
          returning id""",
          job["id"],user_id,data["platform"],data["target_key"],data.get("source_id"),reservation.seconds//60)
        await quota.bind_session(reservation.reservation_id,session_id)

        # SECURITY: ordinary queue jobs never choose a network URL. Resolve it inside
        # the trusted worker from a provider target. This removes the user-controlled
        # manifest -> FFmpeg SSRF path. Manual sources are opt-in for local/staging tests.
        if data["platform"] == "instagram" and not data.get("manual_source"):
            probe = await InstagramLiveProbe(db.pool).probe_top_live(data["target_key"])
            if probe.get("state") != "live":
                return {"skipped": True, "reason": "LIVE_SOURCE_NOT_RESOLVED", "state": probe.get("state")}
            source = validate_provider_source(probe["source"])
        elif settings.live_allow_manual_source and data.get("manual_source"):
            source = validate_provider_source(LiveSource(
                data["platform"], data["target_key"], data.get("source_id", "manual"),
                data["manifest_url"], data.get("headers")
            ))
        else:
            return {"skipped": True, "reason": "LIVE_SOURCE_UNTRUSTED"}
        storage=R2Storage(settings.r2_account_id,settings.r2_access_key_id,settings.r2_secret_access_key,settings.r2_bucket)
        result=await LiveRecorder(db.pool,storage).record(session_id,user_id,source,reservation.seconds)
        await quota.settle(reservation.reservation_id,result["seconds"])
        return {"live_session_id":str(session_id),**result}
    except BaseException:
        # Reconcile uploaded segments even when FFmpeg/worker logic fails after partial capture.
        if reservation and reservation.allowed:
            actual=0
            if session_id:
                actual=int(await con.fetchval(
                    "select coalesce(sum(duration_seconds),0) from live_segments where live_session_id=$1",session_id) or 0)
                await con.execute(
                    """update live_sessions set status='failed',ended_at=coalesce(ended_at,now()),
                    recorded_seconds=greatest(recorded_seconds,$2),stop_reason=coalesce(stop_reason,'worker_error'),
                    error_code=coalesce(error_code,'WORKER_ERROR') where id=$1""",session_id,actual)
            await quota.settle(reservation.reservation_id,actual)
        raise
    finally:
        await con.execute("select pg_advisory_unlock(hashtext('social-saver:live-recorder'))")
        await db.pool.release(con)
