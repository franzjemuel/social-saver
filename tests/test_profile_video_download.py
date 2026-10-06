import re
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from core.storage import R2Storage


PROFILE = UUID("11111111-1111-1111-1111-111111111111")
POST = UUID("22222222-2222-2222-2222-222222222222")


class OwnedRepo:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def get_owned_archived_post_playback(self, user_id, profile_id, post_id):
        self.calls.append((user_id, profile_id, post_id))
        return self.row


class SigningStorage:
    def __init__(self):
        self.download_calls = []
        self.playback_calls = []

    async def presigned_download(self, *args):
        self.download_calls.append(args)
        return "https://signed.invalid/download"

    async def presigned_get(self, *args):
        self.playback_calls.append(args)
        return "https://signed.invalid/playback"


def archived_video_row(**overrides):
    row = {
        "platform": "tiktok",
        "platform_post_id": "1234567890",
        "media_type": "video",
        "storage_key": "private/object",
        "content_type": "video/mp4",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_owned_archived_video_mints_attachment_download(monkeypatch):
    repo = OwnedRepo(archived_video_row())
    storage = SigningStorage()
    monkeypatch.setattr(api, "_repo", repo)
    monkeypatch.setattr(api, "_r2", storage)

    result = await api.profile_archive_download(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant"},
    )

    assert result.available is True
    assert result.download_url == "https://signed.invalid/download"
    assert result.expires_in == api.settings.archive_presign_seconds
    assert repo.calls == [("tenant", PROFILE, POST)]
    assert storage.download_calls == [
        ("private/object", "social-saver-tiktok-1234567890.mp4", "video/mp4", api.settings.archive_presign_seconds)
    ]


@pytest.mark.asyncio
async def test_playback_remains_a_non_attachment_presign(monkeypatch):
    storage = SigningStorage()
    monkeypatch.setattr(api, "_repo", OwnedRepo(archived_video_row()))
    monkeypatch.setattr(api, "_r2", storage)

    result = await api.profile_archive_playback(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant"},
    )

    assert result.available is True
    assert storage.playback_calls == [("private/object", api.settings.archive_presign_seconds)]
    assert storage.download_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    archived_video_row(storage_key=None),
    archived_video_row(media_type="photo"),
])
async def test_unarchived_or_unsupported_post_cannot_mint_download(monkeypatch, row):
    storage = SigningStorage()
    monkeypatch.setattr(api, "_repo", OwnedRepo(row))
    monkeypatch.setattr(api, "_r2", storage)

    result = await api.profile_archive_download(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant"},
    )

    assert result.available is False
    assert result.download_url is None
    assert storage.download_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_id,post_id", [
    ("not-a-uuid", str(POST)),
    (str(PROFILE), "not-a-uuid"),
    (str(PROFILE), str(POST)),
])
async def test_download_hides_malformed_and_foreign_resources(monkeypatch, profile_id, post_id):
    storage = SigningStorage()
    monkeypatch.setattr(api, "_repo", OwnedRepo(None))
    monkeypatch.setattr(api, "_r2", storage)

    with pytest.raises(HTTPException) as error:
        await api.profile_archive_download(profile_id, post_id, identity={"app_user_id": "other"})
    assert error.value.status_code == 404
    assert error.value.detail == "profile_archive_not_found"
    assert storage.download_calls == []


@pytest.mark.asyncio
async def test_download_signing_failure_is_sanitized(monkeypatch):
    class BrokenStorage:
        async def presigned_download(self, *_):
            raise RuntimeError("private storage detail")

    monkeypatch.setattr(api, "_repo", OwnedRepo(archived_video_row()))
    monkeypatch.setattr(api, "_r2", BrokenStorage())
    with pytest.raises(HTTPException) as error:
        await api.profile_archive_download(
            str(PROFILE), str(POST), identity={"app_user_id": "tenant"},
        )
    assert error.value.status_code == 503
    assert error.value.detail == "archive_download_unavailable"


def test_download_filename_is_server_sanitized_and_uses_no_caption_text():
    filename = api._profile_video_download_filename("tik/tok", "../unsafe\\caption text")
    assert filename.endswith(".mp4")
    assert "/" not in filename and "\\" not in filename and ".." not in filename
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", filename)


@pytest.mark.asyncio
async def test_presigned_download_uses_attachment_response_overrides():
    calls = []

    class Client:
        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            calls.append((operation, Params, ExpiresIn))
            return "https://signed.invalid/attachment"

    storage = R2Storage.__new__(R2Storage)
    storage.client = Client()
    storage.bucket = "private-bucket"
    storage.presign_seconds = 900

    url = await storage.presigned_download("private/object", "social-saver-tiktok-1.mp4", "video/mp4", 300)
    assert url == "https://signed.invalid/attachment"
    assert calls == [(
        "get_object",
        {
            "Bucket": "private-bucket",
            "Key": "private/object",
            "ResponseContentDisposition": 'attachment; filename="social-saver-tiktok-1.mp4"',
            "ResponseContentType": "video/mp4",
        },
        300,
    )]
