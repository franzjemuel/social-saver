"""Tenant-scoped HTTP API for the Telegram Mini App.

The browser never supplies an app_user_id. Every request is mapped from a
server-verified Telegram Mini App initData payload to the internal tenant.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import time
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from core.config import settings
from core.database import Database
from core.repository import ProfileImportIdempotencyConflict, Repository
from core.queue import JobQueue
from core.rate_limits import AbuseLimiter
from core.entitlements import EntitlementService
from core.save_requests import normalize_save_url
from providers.base import UnsupportedUrl
from providers.tiktok.provider import normalize_tiktok_profile_target
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


class ProfileArchiveSummary(BaseModel):
    id: str
    platform: str
    platform_account_id: str
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    first_archived_at: datetime
    last_observed_at: datetime
    post_count: int
    present_post_count: int
    removed_post_count: int
    latest_post_at: datetime | None = None


class ProfileArchiveListResponse(BaseModel):
    items: list[ProfileArchiveSummary]
    limit: int
    offset: int


class ProfilePostEngagement(BaseModel):
    observed_at: datetime
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    share_count: int | None = None
    save_count: int | None = None


class ProfileArchivePost(BaseModel):
    id: str
    platform_post_id: str
    original_url: str
    media_type: str
    caption: str | None = None
    published_at: datetime | None = None
    thumbnail_url: str | None = None
    is_present_on_original: bool
    first_archived_at: datetime
    last_observed_at: datetime
    has_archived_media: bool = False
    engagement: ProfilePostEngagement | None = None


class ProfileArchivePostListResponse(BaseModel):
    items: list[ProfileArchivePost]
    limit: int
    offset: int


class TikTokProfileValidationRequest(BaseModel):
    # Keep parsing deliberately permissive so malformed client values become
    # our stable application error instead of Pydantic/parser internals.
    target: object | None = None


class TikTokProfilePreview(BaseModel):
    platform: str
    username: str
    display_name: str | None = None
    avatar_url: str | None = None


class ProfileImportReadyProfile(TikTokProfilePreview):
    id: str


class ProfileImportValidationCreated(BaseModel):
    job_id: str
    phase: str = "validating"


class ProfileImportConfirmationCreated(BaseModel):
    job_id: str
    phase: str


class ProfileImportValidationStatus(BaseModel):
    job_id: str
    phase: str
    preview: TikTokProfilePreview | None = None
    error_code: str | None = None
    profile: ProfileImportReadyProfile | None = None
    posts_imported: int | None = None


_PROFILE_IMPORT_ERROR_CODES = {
    "INVALID_TARGET": "invalid_target",
    "PROFILE_PRIVATE": "profile_private",
    "PROFILE_NOT_FOUND": "profile_not_found",
    "PROVIDER_RATE_LIMITED": "provider_rate_limited",
    "PROFILE_CHANGED": "profile_changed",
    "IMPORT_FAILED": "import_failed",
}


def _profile_import_failure_code(value: object) -> str:
    return _PROFILE_IMPORT_ERROR_CODES.get(str(value or "").upper(), "temporarily_unavailable")


def _safe_tiktok_preview(result: object) -> TikTokProfilePreview | None:
    if not isinstance(result, dict):
        return None
    preview = result.get("preview")
    if not isinstance(preview, dict):
        return None
    username = preview.get("username")
    if not isinstance(username, str) or not username:
        return None
    display_name = preview.get("display_name")
    avatar_url = preview.get("avatar_url")
    return TikTokProfilePreview(
        platform="tiktok",
        username=username,
        display_name=display_name if isinstance(display_name, str) else None,
        # This provider-hosted image is an ephemeral preview, not archive media.
        avatar_url=avatar_url if isinstance(avatar_url, str) else None,
    )


async def _profile_import_status_for_row(row, user_id):
    """Render only the browser contract for an owned validation/import job."""
    if row["job_type"] == "validate_profile_import":
        if row["status"] in {"queued", "running"}:
            return ProfileImportValidationStatus(job_id=str(row["id"]), phase="validating")
        if row["status"] == "completed":
            preview = _safe_tiktok_preview(row["result"])
            if preview is not None:
                return ProfileImportValidationStatus(job_id=str(row["id"]), phase="awaiting_confirmation", preview=preview)
    elif row["job_type"] == "import_profile":
        if row["status"] == "queued":
            return ProfileImportValidationStatus(job_id=str(row["id"]), phase="queued")
        if row["status"] == "running":
            return ProfileImportValidationStatus(job_id=str(row["id"]), phase="importing")
        if row["status"] == "completed" and isinstance(row["result"], dict):
            profile_id = row["result"].get("profile_id")
            if isinstance(profile_id, str) and _repo is not None:
                safe = await _repo.get_owned_archived_profile_import_projection(user_id, profile_id)
                if safe is not None:
                    return ProfileImportValidationStatus(
                        job_id=str(row["id"]), phase="ready",
                        profile=ProfileImportReadyProfile(
                            id=str(safe["id"]), platform="tiktok", username=safe["username"],
                            display_name=safe["display_name"], avatar_url=safe["avatar_url"],
                        ),
                        posts_imported=row["result"].get("posts_imported") if isinstance(row["result"].get("posts_imported"), int) else 0,
                    )
    return ProfileImportValidationStatus(
        job_id=str(row["id"]), phase="failed", error_code=_profile_import_failure_code(row["error_code"]),
    )


def _profile_archive_summary(row) -> ProfileArchiveSummary:
    data = dict(row)
    data["id"] = str(data["id"])
    return ProfileArchiveSummary.model_validate(data)


def _profile_archive_post(row) -> ProfileArchivePost:
    data = dict(row)
    engagement_observed_at = data.pop("engagement_observed_at")
    engagement = None
    if engagement_observed_at is not None:
        engagement = ProfilePostEngagement(
            observed_at=engagement_observed_at,
            view_count=data.pop("view_count"),
            like_count=data.pop("like_count"),
            comment_count=data.pop("comment_count"),
            repost_count=data.pop("repost_count"),
            share_count=data.pop("share_count"),
            save_count=data.pop("save_count"),
        )
    else:
        for key in ("view_count", "like_count", "comment_count", "repost_count", "share_count", "save_count"):
            data.pop(key)
    data["id"] = str(data["id"])
    data["engagement"] = engagement
    return ProfileArchivePost.model_validate(data)


@app.get("/v1/profile-archives", response_model=ProfileArchiveListResponse)
async def profile_archives(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity=Depends(current_identity),
):
    rows = await _repo.list_owned_archived_profiles(
        identity["app_user_id"], limit=limit, offset=offset,
    )
    return ProfileArchiveListResponse(
        items=[_profile_archive_summary(row) for row in rows], limit=limit, offset=offset,
    )


@app.get("/v1/profile-archives/{profile_id}", response_model=ProfileArchiveSummary)
async def profile_archive_detail(profile_id: str, identity=Depends(current_identity)):
    try:
        parsed = UUID(profile_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_archive_not_found") from None
    row = await _repo.get_owned_archived_profile(identity["app_user_id"], parsed)
    if row is None:
        # The same response for foreign and nonexistent IDs avoids an ownership oracle.
        raise HTTPException(status_code=404, detail="profile_archive_not_found")
    return _profile_archive_summary(row)


@app.get("/v1/profile-archives/{profile_id}/posts", response_model=ProfileArchivePostListResponse)
async def profile_archive_posts(
    profile_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity=Depends(current_identity),
):
    try:
        parsed = UUID(profile_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_archive_not_found") from None
    rows = await _repo.list_owned_archived_profile_posts(
        identity["app_user_id"], parsed, limit=limit, offset=offset,
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="profile_archive_not_found")
    return ProfileArchivePostListResponse(
        items=[_profile_archive_post(row) for row in rows], limit=limit, offset=offset,
    )


@app.post(
    "/v1/profile-imports/tiktok/validations",
    response_model=ProfileImportValidationCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_tiktok_profile_validation(
    body: TikTokProfileValidationRequest,
    identity=Depends(current_identity),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Queue worker-owned validation of one public TikTok profile target."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="idempotency_key_required")
    if not isinstance(body.target, str):
        raise HTTPException(status_code=422, detail="invalid_target")
    try:
        _, target = normalize_tiktok_profile_target(body.target)
    except UnsupportedUrl:
        raise HTTPException(status_code=422, detail="invalid_target") from None
    if _repo is None or _queue is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        job = await _repo.create_owned_profile_import_validation_job(
            identity["app_user_id"],
            identity["telegram_user_id"],
            target,
            idempotency_key,
            queue_name=_queue.queue_name,
        )
    except ProfileImportIdempotencyConflict:
        raise HTTPException(status_code=409, detail="profile_import_idempotency_conflict") from None
    return ProfileImportValidationCreated(job_id=str(job.id))


@app.get("/v1/profile-imports/{job_id}", response_model=ProfileImportValidationStatus)
async def profile_import_validation_status(job_id: str, identity=Depends(current_identity)):
    """Project validation state without exposing job input or provider internals."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        parsed = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_import_not_found") from None
    row = await _repo.get_owned_profile_import_workflow(identity["app_user_id"], parsed)
    if row is None:
        # Foreign and nonexistent ids deliberately share the same response.
        raise HTTPException(status_code=404, detail="profile_import_not_found")
    return await _profile_import_status_for_row(row, identity["app_user_id"])


@app.post(
    "/v1/profile-imports/{validation_job_id}/confirm",
    response_model=ProfileImportConfirmationCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_profile_import(validation_job_id: str, identity=Depends(current_identity)):
    """Confirm an owned preview; target/identity/limit are never browser inputs."""
    if _repo is None or _queue is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        parsed = UUID(validation_job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_import_not_found") from None
    job = await _repo.create_owned_profile_import_job(
        identity["app_user_id"], identity["telegram_user_id"], parsed, queue_name=_queue.queue_name,
    )
    if job is None:
        # Invalid states, foreign jobs, and missing IDs intentionally share this.
        raise HTTPException(status_code=404, detail="profile_import_not_found")
    return ProfileImportConfirmationCreated(job_id=str(job.id), phase="ready" if job.ready else "queued")


@app.get("/v1/profile-imports", response_model=ProfileImportValidationStatus | None)
async def active_profile_import(active: bool = False, identity=Depends(current_identity)):
    if not active:
        raise HTTPException(status_code=404, detail="profile_import_not_found")
    if _repo is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    row = await _repo.get_owned_active_profile_import_workflow(identity["app_user_id"])
    return None if row is None else await _profile_import_status_for_row(row, identity["app_user_id"])


class ProfileArchiveMediaJobResponse(BaseModel):
    job_id: str
    status: str = "queued"


class ProfileArchivePlaybackResponse(BaseModel):
    available: bool
    media_type: str
    playback_url: str | None = None
    expires_in: int | None = None


@app.post("/v1/profile-archives/{profile_id}/archive-media", response_model=ProfileArchiveMediaJobResponse,
          status_code=status.HTTP_202_ACCEPTED)
async def archive_profile_media(profile_id: str, identity=Depends(current_identity)):
    if _repo is None or _queue is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        parsed = UUID(profile_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_archive_not_found") from None
    job = await _repo.create_owned_profile_media_job(
        identity["app_user_id"], identity["telegram_user_id"], parsed, queue_name=_queue.queue_name,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="profile_archive_not_found")
    job_id = getattr(job, "id", job)
    return ProfileArchiveMediaJobResponse(job_id=str(job_id))


@app.post("/v1/profile-archives/{profile_id}/posts/{post_id}/archive-media",
          response_model=ProfileArchiveMediaJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def archive_profile_post_media(profile_id: str, post_id: str, identity=Depends(current_identity)):
    if _repo is None or _queue is None:
        raise HTTPException(status_code=503, detail="api_not_ready")
    try:
        profile = UUID(profile_id)
        post = UUID(post_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_archive_not_found") from None
    job = await _repo.create_owned_profile_media_job(
        identity["app_user_id"], identity["telegram_user_id"], profile, post, queue_name=_queue.queue_name,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="profile_archive_not_found")
    job_id = getattr(job, "id", job)
    return ProfileArchiveMediaJobResponse(job_id=str(job_id))


@app.post("/v1/profile-archives/{profile_id}/posts/{post_id}/playback",
          response_model=ProfileArchivePlaybackResponse)
async def profile_archive_playback(profile_id: str, post_id: str, identity=Depends(current_identity)):
    if _repo is None or _r2 is None:
        raise HTTPException(status_code=503, detail="archive_playback_unavailable")
    try:
        profile = UUID(profile_id)
        post = UUID(post_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="profile_archive_not_found") from None
    row = await _repo.get_owned_archived_post_playback(identity["app_user_id"], profile, post)
    if row is None:
        raise HTTPException(status_code=404, detail="profile_archive_not_found")
    if row["storage_key"] is None:
        return ProfileArchivePlaybackResponse(available=False, media_type=row["media_type"])
    try:
        url = await _r2.presigned_get(row["storage_key"], settings.archive_presign_seconds)
    except Exception:
        raise HTTPException(status_code=503, detail="archive_playback_unavailable") from None
    return ProfileArchivePlaybackResponse(
        available=True, media_type=row["media_type"], playback_url=url,
        expires_in=settings.archive_presign_seconds,
    )


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
    unlimited = await _entitlements.is_unlimited(uid)
    if body.archive:
        archive_decision = await _entitlements.authorize_archive(uid)
        if not archive_decision.allowed:
            raise HTTPException(status_code=403, detail=archive_decision.reason.lower())
    plan = await _entitlements.plan_for(uid)
    if not unlimited:
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
