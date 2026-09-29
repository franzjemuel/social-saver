"""Tenant-scoped HTTP API for the Telegram Mini App.

The browser never supplies an app_user_id. Every request is mapped from a
server-verified Telegram Mini App initData payload to the internal tenant.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from core.config import settings
from core.database import Database
from core.repository import Repository
from core.queue import JobQueue
from core.rate_limits import AbuseLimiter
from core.entitlements import EntitlementService
from core.save_requests import normalize_save_url
from providers.base import UnsupportedUrl
from core.telegram_webapp_auth import TelegramWebAppAuthError, verify_telegram_init_data
from core.storage import R2Storage

_db: Database | None = None
_repo: Repository | None = None
_queue: JobQueue | None = None
_entitlements: EntitlementService | None = None
_abuse: AbuseLimiter | None = None
_r2: R2Storage | None = None


def _extract_init_data(authorization: str | None, x_telegram_init_data: str | None) -> str:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "tma" and value:
            return value
    if x_telegram_init_data:
        return x_telegram_init_data
    raise HTTPException(status_code=401, detail="telegram_auth_required")


async def current_identity(
    authorization: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
):
    raw = _extract_init_data(authorization, x_telegram_init_data)
    try:
        identity = verify_telegram_init_data(raw, settings.telegram_bot_token)
    except (TelegramWebAppAuthError, ValueError):
        raise HTTPException(status_code=401, detail="invalid_telegram_auth") from None

    if _repo is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    app_user_id = await _repo.get_or_create_telegram_user(
        identity.telegram_user_id, identity.username, identity.first_name
    )
    deletion = await _repo.get_account_deletion_state(app_user_id)
    if deletion and deletion["deletion_requested_at"] is not None:
        raise HTTPException(status_code=410, detail="account_deletion_in_progress")
    return {"app_user_id": app_user_id, "telegram_user_id": identity.telegram_user_id}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db, _repo, _queue, _entitlements, _abuse, _r2
    _db = Database(settings.database_url)
    await _db.connect()
    _repo = Repository(_db.pool)
    _queue = JobQueue(_db.pool, settings.queue_name, settings.queue_visibility_seconds)
    _entitlements = EntitlementService(_db.pool)
    _abuse = AbuseLimiter()
    # API gets only an R2 token scoped to Object Read for archive link signing.
    # Use separate credentials from the worker write token in staging/production.
    if all((settings.r2_account_id, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket)):
        _r2 = R2Storage(settings.r2_account_id, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket, settings.archive_presign_seconds)
    yield
    await _db.close()
    _repo = None
    _queue = None
    _entitlements = None
    _abuse = None
    _r2 = None
    _db = None


app = FastAPI(title="Social Saver API", version="3.5.0", lifespan=lifespan)

# Browser access is deny-by-default. Production should name the exact Lovable/custom
# domain rather than using a wildcard, because the Mini App sends an Authorization header.
_cors_origins = [o.strip().rstrip("/") for o in settings.api_cors_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Telegram-Init-Data"],
        max_age=600,
    )


@app.get("/health")
async def health():
    if _db is None or _db.pool is None:
        raise HTTPException(status_code=503, detail="database_not_ready")
    try:
        await _db.pool.fetchval("select 1")
    except Exception:
        raise HTTPException(status_code=503, detail="database_unavailable") from None
    return {"ok": True, "service": "social-saver-api"}


@app.get("/v1/me")
async def me(identity=Depends(current_identity)):
    return {"user_id": str(identity["app_user_id"])}


@app.get("/v1/dashboard")
async def dashboard(identity=Depends(current_identity)):
    row = await _repo.dashboard_summary(identity["app_user_id"])
    return jsonable_encoder(dict(row))


@app.get("/v1/archive")
async def archive(
    limit: int = Query(default=25, ge=1, le=100),
    identity=Depends(current_identity),
):
    rows = await _repo.list_archive_entries(identity["app_user_id"], limit=limit)
    return {"items": jsonable_encoder([dict(row) for row in rows])}


@app.get("/v1/archive/{entry_id}")
async def archive_detail(entry_id: str, identity=Depends(current_identity)):
    """Return an owned archive entry and its assets without exposing storage keys."""
    try:
        import uuid
        parsed = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="archive_not_found") from None
    entry = await _repo.get_owned_archive_entry(identity["app_user_id"], parsed)
    if entry is None:
        raise HTTPException(status_code=404, detail="archive_not_found")
    assets = await _repo.list_archive_assets_safe(identity["app_user_id"], parsed)
    return {"entry": jsonable_encoder(dict(entry)), "assets": jsonable_encoder([dict(a) for a in assets])}


@app.delete("/v1/archive/{entry_id}", status_code=status.HTTP_202_ACCEPTED)
async def archive_delete(entry_id: str, identity=Depends(current_identity)):
    """Hide an owned archive entry immediately and queue physical cleanup for the worker."""
    if _repo is None or _queue is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        import uuid
        parsed = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="archive_not_found") from None
    orphaned = await _repo.soft_delete_archive_entry(identity["app_user_id"], parsed)
    if orphaned is None:
        raise HTTPException(status_code=404, detail="archive_not_found")
    jid = await _repo.create_job(
        identity["app_user_id"], identity["telegram_user_id"], "purge_archive",
        {"archive_entry_id": str(parsed)}, source_channel="mini_app",
    )
    await _queue.send(str(jid))
    return {"status": "deletion_queued", "job_id": str(jid)}


@app.post("/v1/archive/{entry_id}/assets/{asset_id}/download")
async def archive_download(entry_id: str, asset_id: str, identity=Depends(current_identity)):
    """Mint a short-lived, single-object R2 GET URL after tenant authorization."""
    if _r2 is None:
        raise HTTPException(status_code=503, detail="archive_download_unavailable")
    try:
        import uuid
        entry = uuid.UUID(entry_id); asset = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="archive_not_found") from None
    row = await _repo.get_owned_archive_object(identity["app_user_id"], entry, asset)
    if row is None:
        raise HTTPException(status_code=404, detail="archive_not_found")
    url = await _r2.presigned_get(row["storage_key"], settings.archive_presign_seconds)
    return {"url": url, "expires_in": settings.archive_presign_seconds}


@app.delete("/v1/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(identity=Depends(current_identity)):
    """Disable the tenant immediately and queue irreversible physical cleanup."""
    if _repo is None or _queue is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    accepted = await _repo.request_account_deletion(identity["app_user_id"])
    if not accepted:
        raise HTTPException(status_code=404, detail="account_not_found")
    jid = await _repo.create_job(
        identity["app_user_id"], identity["telegram_user_id"], "purge_account",
        {}, source_channel="mini_app",
    )
    await _queue.send(str(jid))
    return {"status": "account_deletion_queued", "job_id": str(jid)}


@app.get("/v1/watches")
async def watches(
    limit: int = Query(default=100, ge=1, le=100),
    identity=Depends(current_identity),
):
    rows = await _repo.list_owned_watches(identity["app_user_id"], limit=limit)
    return {"items": jsonable_encoder([dict(row) for row in rows])}


@app.get("/v1/jobs/{job_id}")
async def job_status(job_id: str, identity=Depends(current_identity)):
    """Poll a save job without exposing another tenant's job or internal result payload."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        import uuid
        parsed = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job_not_found") from None
    row = await _repo.get_owned_job_status(identity["app_user_id"], parsed)
    if row is None:
        # Same response for nonexistent and foreign jobs prevents an ownership oracle.
        raise HTTPException(status_code=404, detail="job_not_found")
    return jsonable_encoder(dict(row))


class SaveRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    archive: bool = False


@app.post("/v1/save", status_code=status.HTTP_202_ACCEPTED)
async def save_media(
    body: SaveRequest,
    identity=Depends(current_identity),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Queue a provider-neutral save through the same pipeline as the Telegram bot."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="idempotency_key_required")
    try:
        normalized = normalize_save_url(body.url)
    except UnsupportedUrl:
        raise HTTPException(status_code=422, detail="unsupported_url") from None
    if _repo is None or _queue is None or _entitlements is None or _abuse is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    uid = identity["app_user_id"]
    if body.archive:
        archive_decision = await _entitlements.authorize_archive(uid)
        if not archive_decision.allowed:
            raise HTTPException(status_code=403, detail=archive_decision.reason.lower())
    plan = await _entitlements.plan_for(uid)
    limit = await _abuse.check(uid, plan)
    if not limit.allowed:
        raise HTTPException(status_code=429, detail="rate_limited", headers={"Retry-After": str(max(1, int(limit.reset - time.time())))})
    # A private Telegram chat id equals the Telegram user's id. Delivery therefore
    # remains on the existing worker path and no second media pipeline is created.
    jid = await _repo.create_job(
        uid, identity["telegram_user_id"], "resolve_media",
        {"url": normalized.canonical_url, "requested_platform": normalized.platform, "archive": body.archive},
        source_channel="mini_app", client_request_id=idempotency_key,
    )
    await _queue.send(str(jid))
    return {"job_id": str(jid), "status": "queued", "platform": normalized.platform}
