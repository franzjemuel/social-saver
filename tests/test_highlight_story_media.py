from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from apps.worker.processors.highlight_story_media import process_archive_highlight_story_media
from core.highlight_media import HighlightStoryMediaService
from core.media_download import DownloadedAsset
from providers.base import HighlightStoryMediaDescriptor, SourceUnavailable


PROFILE = UUID("11111111-1111-1111-1111-111111111111")
MEMBERSHIP = UUID("22222222-2222-2222-2222-222222222222")
ASSET = UUID("33333333-3333-3333-3333-333333333333")


class Repo:
    def __init__(self):
        self.seeded = []
        self.attached = []
        self.failures = []
        self.pending = [{"id": ASSET, "position": 0, "asset_type": "video"}]

    async def get_owned_highlight_story_media_target(self, *_):
        return {"username": "owner", "provider_namespace": "synthetic", "provider_collection_id": "c", "provider_item_id": "story", "media_type": "video"}

    async def upsert_owned_highlight_story_media_assets(self, _, __, ___, descriptors):
        self.seeded = list(descriptors)
        return {"inserted": len(descriptors), "refreshed": 0}

    async def list_owned_highlight_story_media_assets(self, *_):
        return list(self.pending)

    async def record_owned_highlight_story_media_failure(self, *args):
        self.failures.append(args)

    async def update_job_progress(self, *_):
        pass


class Provider:
    async def resolve_story_media(self, **_):
        return (HighlightStoryMediaDescriptor(0, "video", ("https://ephemeral.invalid/video",)),)


class Downloader:
    async def download(self, descriptor, destination):
        assert descriptor.candidates == ("https://ephemeral.invalid/video",)
        destination.write_bytes(b"media")
        return DownloadedAsset(destination, 5, "a" * 64, "video/mp4")


class Service:
    async def persist(self, **kwargs):
        assert kwargs["asset_id"] == ASSET
        return False


@pytest.mark.asyncio
async def test_synthetic_story_media_pipeline_keeps_descriptors_in_memory(tmp_path):
    repo = Repo()
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo, Provider(), Downloader(), Service(),
    )
    assert result == {"assets_discovered": 1, "assets_seeded": 1, "attached": 1, "uploaded": 1, "reused": 0, "failures": 0, "pending_after": 0}
    assert repo.seeded[0].candidates == ("https://ephemeral.invalid/video",)
    assert not repo.failures


@pytest.mark.asyncio
async def test_partial_story_failure_preserves_other_position_and_records_safe_code():
    repo = Repo()
    second = UUID("44444444-4444-4444-4444-444444444444")
    repo.pending = [
        {"id": ASSET, "position": 0, "asset_type": "photo"},
        {"id": second, "position": 1, "asset_type": "photo"},
    ]
    class Photos:
        async def resolve_story_media(self, **_):
            return (
                HighlightStoryMediaDescriptor(0, "photo", ("https://ephemeral.invalid/0",)),
                HighlightStoryMediaDescriptor(1, "photo", ("https://ephemeral.invalid/1",)),
            )
    class FailingDownloader:
        async def download(self, descriptor, destination):
            if descriptor.position == 1:
                raise SourceUnavailable("private provider URL")
            destination.write_bytes(b"image")
            return DownloadedAsset(destination, 5, "b" * 64, "image/jpeg")
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo, Photos(), FailingDownloader(), Service(),
    )
    assert result["attached"] == result["uploaded"] == 1
    assert result["failures"] == result["pending_after"] == 1
    assert repo.failures[-1][-1] == "acquisition_or_storage_failed"


@pytest.mark.asyncio
async def test_highlight_persistence_dedupes_and_uses_tenant_attachment(tmp_path):
    file = tmp_path / "video.mp4"; file.write_bytes(b"video")
    downloaded = DownloadedAsset(file, 5, "c" * 64, "video/mp4")
    calls = []
    class PersistenceRepo:
        async def get_stored_object_by_sha(self, _): return {"id": ASSET}
        async def attach_owned_highlight_story_media_object(self, *args): calls.append(args); return ASSET
    reused = await HighlightStoryMediaService(PersistenceRepo(), SimpleNamespace()).persist(
        user_id="tenant", profile_id=PROFILE, asset_id=ASSET, downloaded=downloaded,
    )
    assert reused and calls == [("tenant", PROFILE, ASSET, ASSET)]


@pytest.mark.asyncio
async def test_playback_is_tenant_scoped_and_gated_queue_does_not_enqueue(monkeypatch):
    class ApiRepo:
        async def get_owned_highlight_story_media_playback(self, *_):
            return {"asset_type": "video", "storage_key": "private/key"}
    class Storage:
        async def presigned_get(self, *_): return "https://signed.invalid/short"
    monkeypatch.setattr(api, "_repo", ApiRepo())
    monkeypatch.setattr(api, "_r2", Storage())
    response = await api.highlight_story_media_playback(
        str(PROFILE), str(MEMBERSHIP), 0, identity={"app_user_id": "tenant", "telegram_user_id": 1},
    )
    assert response.available and response.playback_url == "https://signed.invalid/short"
    monkeypatch.setattr(api.settings, "tiktok_highlights_media_enabled", False)
    with pytest.raises(HTTPException) as error:
        await api.archive_highlight_story_media(
            str(PROFILE), str(MEMBERSHIP), identity={"app_user_id": "tenant", "telegram_user_id": 1},
        )
    assert error.value.status_code == 404


def test_highlight_schema_never_persists_descriptor_columns():
    migration = Path("supabase/migrations/025_highlight_story_media.sql").read_text()
    assert "source_url" not in migration and "descriptor" in migration
