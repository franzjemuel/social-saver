from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import apps.api.main as api
from apps.worker.processors.profile_import_validation import process_validate_profile_import
from core.repository import ProfileImportIdempotencyConflict, ProfileImportValidationJob, ProfileImportJob
from providers.base import SourceUnavailable
from providers.tiktok.provider import normalize_tiktok_profile_target
from providers.tiktok.scanner import TikTokProfilePrivate, TikTokScannedProfile
from providers.tiktok.validation import (
    TikTokProfileValidationFailure,
    TikTokProfileValidator,
)


JOB = UUID("11111111-1111-1111-1111-111111111111")


@pytest.mark.parametrize(
    ("target", "username"),
    [
        ("creator", "creator"),
        ("@creator", "creator"),
        ("https://www.tiktok.com/@creator", "creator"),
        ("https://tiktok.com/@creator/", "creator"),
        ("https://m.tiktok.com/@creator/?source=share#preview", "creator"),
    ],
)
def test_tiktok_profile_target_normalizes_safe_profile_forms(target, username):
    assert normalize_tiktok_profile_target(target) == (username, f"https://www.tiktok.com/@{username}")


@pytest.mark.parametrize(
    "target",
    [
        "",
        "   ",
        "https://www.tiktok.com/@creator/video/123",
        "https://www.tiktok.com/@creator/photo/123",
        "https://example.com/@creator",
        "https://vm.tiktok.com/abc",
        "https://www.tiktok.com/@creator/extra",
        "https://attacker@example.com/@creator",
        "@not valid",
    ],
)
def test_tiktok_profile_target_rejects_ambiguous_or_unsafe_forms(target):
    with pytest.raises(Exception) as error:
        normalize_tiktok_profile_target(target)
    assert "TikTok" in str(error.value) or "target" in str(error.value).lower()


class FakeRepo:
    def __init__(self, *, status=None):
        self.calls = []
        self.status = status

    async def create_owned_profile_import_validation_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return ProfileImportValidationJob(JOB, True)

    async def get_owned_profile_import_validation(self, user_id, job_id):
        self.calls.append((user_id, job_id))
        return self.status

    async def get_owned_profile_import_workflow(self, user_id, job_id):
        self.calls.append((user_id, job_id))
        return self.status

    async def get_owned_archived_profile_import_projection(self, user_id, profile_id):
        return None


@pytest.mark.asyncio
async def test_validation_api_requires_authentication_and_safe_target(monkeypatch):
    with pytest.raises(HTTPException) as unauthenticated:
        await api.current_identity(authorization=None, x_telegram_init_data=None)
    assert unauthenticated.value.status_code == 401

    repo = FakeRepo()
    monkeypatch.setattr(api, "_repo", repo)
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="media_jobs"))
    identity = {"app_user_id": "tenant-a", "telegram_user_id": 1}

    response = await api.create_tiktok_profile_validation(
        api.TikTokProfileValidationRequest(target="@creator"),
        identity=identity,
        idempotency_key="request-a",
    )
    assert response.job_id == str(JOB) and response.phase == "validating"
    assert repo.calls[0][0][:4] == ("tenant-a", 1, "https://www.tiktok.com/@creator", "request-a")

    with pytest.raises(HTTPException) as malformed:
        await api.create_tiktok_profile_validation(
            api.TikTokProfileValidationRequest(target="https://www.tiktok.com/@creator/video/1"),
            identity=identity,
            idempotency_key="request-b",
        )
    assert (malformed.value.status_code, malformed.value.detail) == (422, "invalid_target")


@pytest.mark.asyncio
async def test_validation_api_rejects_idempotency_target_conflicts(monkeypatch):
    class ConflictRepo(FakeRepo):
        async def create_owned_profile_import_validation_job(self, *args, **kwargs):
            raise ProfileImportIdempotencyConflict("internal target")

    monkeypatch.setattr(api, "_repo", ConflictRepo())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="media_jobs"))
    with pytest.raises(HTTPException) as conflict:
        await api.create_tiktok_profile_validation(
            api.TikTokProfileValidationRequest(target="creator"),
            identity={"app_user_id": "tenant-a", "telegram_user_id": 1},
            idempotency_key="request-a",
        )
    assert (conflict.value.status_code, conflict.value.detail) == (409, "profile_import_idempotency_conflict")


@pytest.mark.asyncio
async def test_validation_status_is_tenant_scoped_and_sanitized(monkeypatch):
    completed = {
        "id": JOB,
        "job_type": "validate_profile_import",
        "status": "completed",
        "result": {
            "platform_account_id": "internal-id",
            "sec_uid": "internal-secuid",
            "target": "https://www.tiktok.com/@creator",
            "preview": {
                "platform": "tiktok",
                "username": "Creator",
                "display_name": "Creator name",
                "avatar_url": "https://avatar.invalid/image.jpg",
                "raw": "must-not-leak",
            },
        },
        "error_code": None,
    }
    repo = FakeRepo(status=completed)
    monkeypatch.setattr(api, "_repo", repo)
    response = await api.profile_import_validation_status(str(JOB), identity={"app_user_id": "tenant-a"})
    assert response.phase == "awaiting_confirmation"
    assert response.preview.model_dump() == {
        "platform": "tiktok",
        "username": "Creator",
        "display_name": "Creator name",
        "avatar_url": "https://avatar.invalid/image.jpg",
    }
    assert "internal" not in str(response.model_dump())

    repo.status = None
    for job_id in (str(JOB), "22222222-2222-2222-2222-222222222222"):
        with pytest.raises(HTTPException) as missing:
            await api.profile_import_validation_status(job_id, identity={"app_user_id": "tenant-b"})
        assert (missing.value.status_code, missing.value.detail) == (404, "profile_import_not_found")

    repo.status = {"id": JOB, "job_type": "validate_profile_import", "status": "failed", "result": None, "error_code": "https://provider.invalid/raw"}
    failed = await api.profile_import_validation_status(str(JOB), identity={"app_user_id": "tenant-a"})
    assert failed.phase == "failed" and failed.error_code == "temporarily_unavailable"
    assert "https" not in str(failed.model_dump())


class PreviewScanner:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def resolve_profile(self, username):
        self.calls.append(username)
        if self.failure:
            raise self.failure
        return TikTokScannedProfile(
            username="Creator",
            user_id="internal-stable-id",
            sec_uid="internal-secuid",
            display_name="Creator name",
            avatar_url="https://avatar.invalid/image.jpg",
        )


@pytest.mark.asyncio
async def test_validation_worker_preserves_internal_identity_but_projects_safe_preview():
    validator = TikTokProfileValidator(PreviewScanner())
    result = await process_validate_profile_import(
        {"input": {"platform": "tiktok", "target": "@creator"}}, validator,
    )
    assert result["platform_account_id"] == "internal-stable-id"
    assert result["preview"] == {
        "platform": "tiktok",
        "username": "Creator",
        "display_name": "Creator name",
        "avatar_url": "https://avatar.invalid/image.jpg",
    }
    assert "sec_uid" not in result


@pytest.mark.asyncio
async def test_validation_worker_sanitizes_provider_failures():
    for failure, expected in (
        (TikTokProfilePrivate("private provider detail"), "profile_private"),
        (SourceUnavailable("https://provider.invalid/raw"), "temporarily_unavailable"),
    ):
        with pytest.raises(TikTokProfileValidationFailure) as error:
            await TikTokProfileValidator(PreviewScanner(failure)).validate("creator")
        assert error.value.code == expected
        assert "provider" not in str(error.value)


def test_validation_routes_require_telegram_identity():
    routes = {route.path: route for route in api.app.routes if isinstance(route, APIRoute)}
    for path in ("/v1/profile-imports/tiktok/validations", "/v1/profile-imports/{job_id}", "/v1/profile-imports/{validation_job_id}/confirm"):
        assert api.current_identity in [dependency.call for dependency in routes[path].dependant.dependencies]


@pytest.mark.asyncio
async def test_confirmation_derives_owned_validation_only_and_coalesces(monkeypatch):
    class ConfirmRepo(FakeRepo):
        async def create_owned_profile_import_job(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return ProfileImportJob(JOB, False)

    repo = ConfirmRepo()
    monkeypatch.setattr(api, "_repo", repo)
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="media_jobs"))
    response = await api.confirm_profile_import(str(JOB), identity={"app_user_id": "tenant-a", "telegram_user_id": 1})
    assert response.model_dump() == {"job_id": str(JOB), "phase": "queued"}
    assert repo.calls[0][0][:3] == ("tenant-a", 1, JOB)

    class MissingRepo(ConfirmRepo):
        async def create_owned_profile_import_job(self, *args, **kwargs):
            return None
    monkeypatch.setattr(api, "_repo", MissingRepo())
    for value in (str(JOB), "22222222-2222-2222-2222-222222222222"):
        with pytest.raises(HTTPException) as missing:
            await api.confirm_profile_import(value, identity={"app_user_id": "tenant-b", "telegram_user_id": 2})
        assert (missing.value.status_code, missing.value.detail) == (404, "profile_import_not_found")
