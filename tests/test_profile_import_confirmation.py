from uuid import UUID

import pytest

import apps.api.main as api
from apps.worker.processors.profile_import import TikTokProfileImportFailure, process_import_profile
from providers.tiktok.constants import INITIAL_PROFILE_IMPORT_POST_LIMIT
from providers.tiktok.importer import ProfileImportSummary, TikTokProfileChanged


JOB = UUID("33333333-3333-3333-3333-333333333333")
PROFILE = UUID("44444444-4444-4444-4444-444444444444")


class Importer:
    def __init__(self, outcome=None):
        self.outcome = outcome
        self.calls = []

    async def import_profile(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome or ProfileImportSummary(PROFILE, "stable", 12, 12, 0, 0)


class ProgressRepo:
    async def update_job_progress(self, *_):
        return None


@pytest.mark.asyncio
async def test_import_worker_uses_server_limit_and_expected_identity_only():
    importer = Importer()
    result = await process_import_profile({"id": JOB, "user_id": "tenant-a", "input": {
        "platform": "tiktok", "target": "https://www.tiktok.com/@creator",
        "expected_platform_account_id": "stable", "limit": 999999,
    }}, ProgressRepo(), importer)
    assert result == {"profile_id": str(PROFILE), "posts_imported": 12}
    assert importer.calls[0][1]["limit"] == INITIAL_PROFILE_IMPORT_POST_LIMIT
    assert importer.calls[0][1]["expected_platform_account_id"] == "stable"


@pytest.mark.asyncio
async def test_import_worker_maps_changed_identity_without_importing_other_account():
    with pytest.raises(TikTokProfileImportFailure) as failure:
        await process_import_profile({"id": JOB, "user_id": "tenant-a", "input": {
            "platform": "tiktok", "target": "https://www.tiktok.com/@creator",
            "expected_platform_account_id": "stable",
        }}, ProgressRepo(), Importer(TikTokProfileChanged("changed")))
    assert failure.value.code == "profile_changed"


@pytest.mark.asyncio
async def test_ready_status_exposes_only_owned_profile_and_import_count(monkeypatch):
    class ReadyRepo:
        async def get_owned_profile_import_workflow(self, user, job):
            return {"id": JOB, "job_type": "import_profile", "status": "completed", "error_code": None,
                    "result": {"profile_id": str(PROFILE), "posts_imported": 12, "platform_account_id": "secret"}}
        async def get_owned_archived_profile_import_projection(self, user, profile):
            return {"id": PROFILE, "platform": "tiktok", "username": "creator", "display_name": "Creator", "avatar_url": None}
    monkeypatch.setattr(api, "_repo", ReadyRepo())
    response = await api.profile_import_validation_status(str(JOB), identity={"app_user_id": "tenant-a"})
    assert response.phase == "ready" and response.posts_imported == 12
    assert response.profile.id == str(PROFILE)
    assert "secret" not in str(response.model_dump())


@pytest.mark.asyncio
async def test_active_workflow_returns_newest_owned_state(monkeypatch):
    class ActiveRepo:
        async def get_owned_active_profile_import_workflow(self, user):
            return {"id": JOB, "job_type": "import_profile", "status": "running", "result": None, "error_code": None}
    monkeypatch.setattr(api, "_repo", ActiveRepo())
    response = await api.active_profile_import(active=True, identity={"app_user_id": "tenant-a"})
    assert response.phase == "importing" and response.job_id == str(JOB)
