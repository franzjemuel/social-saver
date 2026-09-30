"""Destructive-safe staging canary for Social Saver infrastructure.

Checks the exact external dependencies needed before enabling beta traffic.
Creates only namespaced temporary DB/R2 objects and removes them afterward.
Never prints credentials or signed URLs.
"""
import argparse
import asyncio
import json
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from core.config import settings
from core.database import Database
from core.storage import R2Storage


def result(name, ok, detail, ms):
    return {"check": name, "ok": bool(ok), "detail": detail, "duration_ms": round(ms)}


async def telegram_check():
    started=time.monotonic()
    try:
        req=urllib.request.Request(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            headers={"User-Agent":"social-saver-staging-canary/2.4"},
        )
        payload=await asyncio.to_thread(lambda: json.load(urllib.request.urlopen(req, timeout=10)))
        bot=payload.get("result") or {}
        ok=bool(payload.get("ok") and bot.get("id"))
        return result("telegram_get_me",ok,f"bot=@{bot.get('username','unknown')}" if ok else "Telegram rejected bot token",(time.monotonic()-started)*1000)
    except Exception as exc:
        return result("telegram_get_me",False,type(exc).__name__,(time.monotonic()-started)*1000)


async def database_checks(db):
    out=[]
    started=time.monotonic()
    try:
        v=await db.pool.fetchval("select current_database()")
        out.append(result("postgres",bool(v),"connected",(time.monotonic()-started)*1000))
    except Exception as exc:
        return [result("postgres",False,type(exc).__name__,(time.monotonic()-started)*1000)]

    started=time.monotonic()
    try:
        pgmq=await db.pool.fetchval("select exists(select 1 from pg_extension where extname='pgmq')")
        out.append(result("pgmq_extension",pgmq,"installed" if pgmq else "missing",(time.monotonic()-started)*1000))
    except Exception as exc:
        out.append(result("pgmq_extension",False,type(exc).__name__,(time.monotonic()-started)*1000))

    started=time.monotonic()
    try:
        migrations=await db.pool.fetchval("select count(*) from supabase_migrations.schema_migrations")
        out.append(result("migrations",migrations and migrations>=20,f"applied={migrations}",(time.monotonic()-started)*1000))
    except Exception as exc:
        out.append(result("migrations",False,type(exc).__name__,(time.monotonic()-started)*1000))
    return out


async def queue_roundtrip(db):
    """Probe a private queue in a transaction that is always rolled back.

    Never claim from settings.queue_name: a canary must not hide customer jobs
    or race the real worker. Transactional DDL removes the queue and its archive
    on success, failure or connection loss without a persistent cleanup job.
    """
    started = time.monotonic()
    if settings.app_env != "staging":
        return result("queue_roundtrip", False, "requires APP_ENV=staging", 0)
    queue_name = f"canary_{uuid4().hex}"
    marker = str(uuid4())
    try:
        async with db.pool.acquire() as con:
            transaction = con.transaction()
            await transaction.start()
            try:
                await con.execute("select pgmq.create($1)", queue_name)
                msg_id = await con.fetchval(
                    "select * from pgmq.send($1, $2::text::jsonb, 0)",
                    queue_name, json.dumps({"version": 1, "job_id": marker}),
                )
                messages = await con.fetch(
                    "select * from pgmq.read_with_poll($1,$2,$3,5,100)",
                    queue_name, 30, 1,
                )
                if len(messages) != 1:
                    raise RuntimeError("probe_message_missing")
                message = messages[0]
                body = message["message"]
                if isinstance(body, str):
                    body = json.loads(body)
                if message["msg_id"] != msg_id or body.get("job_id") != marker:
                    raise RuntimeError("probe_message_mismatch")
                archived = await con.fetchval("select pgmq.archive($1::text,$2::bigint)", queue_name, msg_id)
                if not archived:
                    raise RuntimeError("probe_archive_failed")
            finally:
                await transaction.rollback()
        return result("queue_roundtrip", True, "isolated send/read/archive; rolled back", (time.monotonic()-started)*1000)
    except Exception as exc:
        return result("queue_roundtrip", False, type(exc).__name__, (time.monotonic()-started)*1000)


async def r2_roundtrip():
    started = time.monotonic()
    if settings.app_env != "staging":
        return result("r2_roundtrip", False, "requires APP_ENV=staging", 0)
    key = f"canary/{uuid4().hex}.txt"
    storage = None
    ok = False
    detail = "probe_failed"
    try:
        storage = R2Storage(settings.r2_account_id,settings.r2_access_key_id,settings.r2_secret_access_key,settings.r2_bucket,60)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)/"probe.txt"
            dst = Path(td)/"probe.out"
            src.write_text("social-saver-canary", encoding="utf-8")
            await storage.put_file(src, key, "text/plain")
            await storage.download_file(key, dst)
            ok = dst.read_text(encoding="utf-8") == "social-saver-canary"
            detail = "put/get/delete" if ok else "content mismatch"
    except Exception as exc:
        detail = type(exc).__name__
    finally:
        if storage is not None:
            try:
                # Also clean up if the upload succeeded but its response or GET failed.
                await storage.delete(key)
            except Exception as exc:
                ok = False
                detail = f"cleanup_failed:{type(exc).__name__}"
    return result("r2_roundtrip", ok, detail, (time.monotonic()-started)*1000)


async def worker_heartbeat(db, max_age=90):
    started=time.monotonic()
    try:
        row=await db.pool.fetchrow("select worker_id,last_seen_at from worker_heartbeats order by last_seen_at desc limit 1")
        if not row: return result("worker_heartbeat",False,"no heartbeat",(time.monotonic()-started)*1000)
        age=await db.pool.fetchval("select extract(epoch from now()-$1::timestamptz)",row["last_seen_at"])
        ok=float(age)<=max_age
        return result("worker_heartbeat",ok,f"age_seconds={round(float(age))}",(time.monotonic()-started)*1000)
    except Exception as exc:
        return result("worker_heartbeat",False,type(exc).__name__,(time.monotonic()-started)*1000)


async def provider_session(db):
    started=time.monotonic()
    try:
        row=await db.pool.fetchrow("select status,last_validated_at,cooldown_until from provider_sessions where platform='instagram' order by updated_at desc limit 1")
        if not row: return result("instagram_session",False,"no stored Instagram session",(time.monotonic()-started)*1000)
        ok=row["status"]=="healthy" and row["last_validated_at"] is not None
        return result("instagram_session",ok,f"status={row['status']}",(time.monotonic()-started)*1000)
    except Exception as exc:
        return result("instagram_session",False,type(exc).__name__,(time.monotonic()-started)*1000)


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    if settings.app_env != "staging":
        parser.error("canary requires APP_ENV=staging; no external checks executed")
    checks=[]
    checks.append(await telegram_check())
    db=Database(settings.database_url)
    try:
        await db.connect()
        checks.extend(await database_checks(db))
        checks.append(await queue_roundtrip(db))
        checks.append(await worker_heartbeat(db))
        checks.append(await provider_session(db))
    except Exception as exc:
        checks.append(result("database_bootstrap",False,type(exc).__name__,0))
    finally:
        try: await db.close()
        except Exception: pass
    checks.append(await r2_roundtrip())
    summary={"version":"3.3","environment":settings.app_env,"ok":all(c["ok"] for c in checks),"checks":checks}
    if args.json: print(json.dumps(summary,indent=2,default=str))
    else:
        for c in checks: print(("PASS" if c["ok"] else "FAIL"),c["check"],"-",c["detail"])
        print("READY" if summary["ok"] else "NOT READY")
    raise SystemExit(0 if summary["ok"] else 1)

if __name__=="__main__": asyncio.run(main())
