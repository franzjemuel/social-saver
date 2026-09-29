import asyncio, os, socket
from aiogram import Bot
from core.config import settings
from core.database import Database
from core.queue import JobQueue
from core.repository import Repository
from providers.base import TerminalProviderError
from apps.worker.processors.media import process_resolve_media
from apps.worker.processors.watch import process_poll_watch
from apps.worker.processors.live import process_record_live
from apps.worker.processors.live_finalize import process_finalize_live
from apps.worker.processors.archive_delete import process_purge_archive
from apps.worker.processors.account_delete import process_purge_account
from core.observability import init_observability, capture_job_exception
from core.rate_limits import provider_concurrency
from core.readiness import start_readiness_server

async def heartbeat(queue, msg_id, stop):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.queue_heartbeat_seconds)
        except asyncio.TimeoutError:
            await queue.extend_visibility(msg_id, settings.queue_long_job_visibility_seconds)

async def dead_letter(db, queue, repo, msg, job_id, exc):
    code = type(exc).__name__.upper()
    message = str(exc) or code
    payload = {
        "version": 1, "job_id": str(job_id), "source_queue": settings.queue_name,
        "source_msg_id": msg["msg_id"], "read_ct": msg["read_ct"],
        "error_code": code, "error_message": message[:1000],
        "original_message": dict(msg["message"]),
    }
    async with db.pool.acquire() as con:
        async with con.transaction():
            await con.execute(
                """insert into dead_letter_events(job_id,source_queue,source_msg_id,read_count,error_code,error_message,payload)
                values($1,$2,$3,$4,$5,$6,$7) on conflict(source_queue,source_msg_id) do nothing""",
                job_id, settings.queue_name, msg["msg_id"], msg["read_ct"], code, message[:1000], payload
            )
            await repo.fail_job(job_id, code, message)
    await queue.send_dead_letter(payload)
    await queue.archive(msg["msg_id"])

async def main():
    init_observability("media-worker")
    db = Database(settings.database_url); await db.connect()
    repo = Repository(db.pool)
    queue = JobQueue(db.pool, settings.queue_name, settings.queue_visibility_seconds)
    bot = Bot(settings.telegram_bot_token)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    await db.pool.execute(
      """insert into worker_heartbeats(worker_id,service,metadata)
      values($1,'media-worker',$2)
      on conflict(worker_id) do update set last_seen_at=now(),metadata=excluded.metadata""",
      worker_id, {"hostname":socket.gethostname(),"pid":os.getpid()})

    async def ready_probe():
        try:
            ok = await db.pool.fetchval("select 1")
            return bool(ok == 1), "database_ready"
        except Exception:
            return False, "database_unavailable"

    readiness_server = await start_readiness_server(ready_probe, port=settings.port)

    while True:
        await db.pool.execute(
          "update worker_heartbeats set last_seen_at=now() where worker_id=$1",worker_id)
        messages = await queue.claim(1)
        for msg in messages:
            job_id = msg["message"]["job_id"]
            job = await repo.get_job(job_id)
            if not job or job["status"] == "completed":
                await queue.archive(msg["msg_id"]); continue
            if msg["read_ct"] > settings.queue_max_attempts:
                exc = RuntimeError("Maximum delivery attempts exceeded")
                await dead_letter(db, queue, repo, msg, job_id, exc)
                await bot.send_message(job["telegram_chat_id"], "❌ This job could not be completed after several attempts.")
                continue

            stop = asyncio.Event()
            hb = asyncio.create_task(heartbeat(queue, msg["msg_id"], stop))
            try:
                await repo.start_job(job_id)
                if job["job_type"] == "test":
                    await asyncio.sleep(2)
                    result = {"message":"Worker test successful","worker_id":worker_id,"attempt":msg["read_ct"]}
                    success_message = None
                elif job["job_type"] == "resolve_media":
                    async with provider_concurrency.for_platform("instagram"):
                        result = await process_resolve_media(job, repo, bot, db.pool)
                    success_message = None
                elif job["job_type"] == "poll_watch":
                    async with provider_concurrency.for_platform("instagram"):
                        result = await process_poll_watch(job, db)
                    success_message = None
                elif job["job_type"] == "record_live":
                    async with provider_concurrency.for_platform("instagram"):
                        result = await process_record_live(job, db)
                    success_message = None
                elif job["job_type"] == "finalize_live":
                    result = await process_finalize_live(job, db, bot)
                    success_message = None
                elif job["job_type"] == "purge_archive":
                    result = await process_purge_archive(job, repo)
                    success_message = None
                elif job["job_type"] == "purge_account":
                    result = await process_purge_account(job, repo)
                    success_message = None
                else:
                    raise ValueError(f"Unknown job type: {job['job_type']}")

                await repo.complete_job(job_id, result)
                if success_message:
                    await bot.send_message(job["telegram_chat_id"], success_message)
                await queue.archive(msg["msg_id"])
            except TerminalProviderError as exc:
                code = type(exc).__name__.upper()
                await repo.fail_job(job_id, code, str(exc))
                await bot.send_message(job["telegram_chat_id"], f"❌ {str(exc)}")
                await queue.archive(msg["msg_id"])
            except Exception as exc:
                capture_job_exception(exc,job)
                if msg["read_ct"] >= settings.queue_max_attempts:
                    await dead_letter(db, queue, repo, msg, job_id, exc)
                    await bot.send_message(job["telegram_chat_id"], "❌ Instagram is temporarily unavailable for this link. Please try again later.")
                # Otherwise do not archive: VT expiry is the retry mechanism.
            finally:
                stop.set(); hb.cancel()
                try: await hb
                except asyncio.CancelledError: pass

if __name__ == "__main__": asyncio.run(main())
