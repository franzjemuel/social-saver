from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from apps.worker.processors.profile_archive_media import process_archive_profile_media
from core.media_download import DownloadedAsset
from core.profile_archive_media import ProfileArchiveMediaService

PROFILE = UUID("11111111-1111-1111-1111-111111111111")
POST = UUID("22222222-2222-2222-2222-222222222222")
ASSET = UUID("33333333-3333-3333-3333-333333333333")


class Repo:
    def __init__(self, owned=True, stored=None): self.owned, self.stored, self.calls = owned, stored, []
    async def create_owned_profile_media_job(self, user, chat, profile, post_id=None):
        self.calls.append((user, chat, profile, post_id)); return UUID("44444444-4444-4444-4444-444444444444") if self.owned else None
    async def get_owned_archived_post_playback(self, user, profile, post):
        return {"media_type": "video", "storage_key": "private/key"} if self.owned else None
    async def list_owned_archived_video_assets(self, user, profile, post_id=None):
        return [{"id": ASSET, "asset_type": "video", "original_url": "https://www.tiktok.com/@a/video/1"}, {"id": UUID("55555555-5555-5555-5555-555555555555"), "asset_type": "photo", "original_url": "https://www.tiktok.com/@a/photo/2"}]
    async def get_stored_object_by_sha(self, sha): return self.stored
    async def create_stored_object(self, *args): self.calls.append(("create", args)); return UUID("66666666-6666-6666-6666-666666666666")
    async def attach_owned_archived_post_media_object(self, *args): self.calls.append(("attach", args)); return ASSET


@pytest.mark.asyncio
async def test_queue_and_playback_are_tenant_scoped_and_safe(monkeypatch):
    repo, queue = Repo(), SimpleNamespace(send=lambda _: None)
    async def send(_): pass
    queue.send = send
    monkeypatch.setattr(api, "_repo", repo); monkeypatch.setattr(api, "_queue", queue)
    result = await api.archive_profile_media(str(PROFILE), identity={"app_user_id": "tenant", "telegram_user_id": 1})
    assert result.status == "queued" and repo.calls[0][0] == "tenant"
    monkeypatch.setattr(api, "_r2", SimpleNamespace(presigned_get=lambda *_: None))
    async def sign(*_): return "https://signed.invalid/temporary"
    api._r2.presigned_get = sign
    playback = await api.profile_archive_playback(str(PROFILE), str(POST), identity={"app_user_id": "tenant"})
    assert playback.available and set(playback.model_dump()) == {"available", "media_type", "playback_url", "expires_in"}
    monkeypatch.setattr(api, "_repo", Repo(owned=False))
    with pytest.raises(HTTPException) as error:
        await api.archive_profile_media(str(PROFILE), identity={"app_user_id": "other", "telegram_user_id": 2})
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_worker_persists_video_reuses_sha_and_skips_photo(tmp_path):
    media = tmp_path / "video.mp4"; media.write_bytes(b"video")
    downloaded = DownloadedAsset(media, 5, "a" * 64, "video/mp4")
    class Downloader:
        async def download_post(self, url, path): return downloaded
    class Service:
        async def persist(self, **kwargs): return False
    result = await process_archive_profile_media({"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE}}, Repo(), Downloader(), Service())
    assert result == {"profile_id": str(PROFILE), "post_id": None, "attached": 1, "uploaded": 1, "reused": 0, "skipped": 1, "failures": 0}
    storage = SimpleNamespace(put_file=None)
    uploads = []
    async def put(path, key, content): uploads.append((path, key)); return None
    storage.put_file = put
    repo = Repo(stored={"id": UUID("77777777-7777-7777-7777-777777777777"), "storage_key": "archive/existing"})
    reused = await ProfileArchiveMediaService(repo, storage).persist(user_id="tenant", profile_id=PROFILE, asset_id=ASSET, downloaded=downloaded)
    assert reused is True and not uploads and any(call[0] == "attach" for call in repo.calls)


def test_schema_and_projection_keep_private_storage_internal():
    migration = Path("supabase/migrations/023_profile_archive_media.sql").read_text()
    source = Path("core/repository.py").read_text()
    assert "stored_object_id" in migration
    assert "has_archived_media" in source
    assert "profile.user_id=$2" in source
    assert "archived_post_media_assets profile_asset" in source
    assert "union" in source and "archived_post_media_assets asset" in source


@pytest.mark.asyncio
async def test_single_post_route_and_worker_never_fall_through(monkeypatch):
    repo = Repo()
    async def send(_): pass
    monkeypatch.setattr(api, "_repo", repo); monkeypatch.setattr(api, "_queue", SimpleNamespace(send=send))
    response = await api.archive_profile_post_media(str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 1})
    assert response.status == "queued" and repo.calls[-1] == ("tenant", 1, PROFILE, POST)
    class TargetedRepo(Repo):
        async def list_owned_archived_video_assets(self, user, profile, post_id=None):
            assert post_id == POST
            return []  # persisted target is an idempotent no-op
    result = await process_archive_profile_media({"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE, "post_id": POST}}, TargetedRepo())
    assert result["post_id"] == str(POST) and result["attached"] == 0


@pytest.mark.asyncio
async def test_profile_media_failure_propagates_for_retry():
    class FailingDownloader:
        async def download_post(self, url, path): raise RuntimeError("network")
    with pytest.raises(Exception) as error:
        await process_archive_profile_media({"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE, "post_id": POST}}, Repo(), FailingDownloader(), SimpleNamespace())
    assert "Profile media persistence incomplete" in str(error.value)
