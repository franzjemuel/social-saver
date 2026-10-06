from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api


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
        self.calls = []

    async def presigned_get(self, *args):
        self.calls.append(args)
        return "https://signed.invalid/temporary"


class PreparedMessageBot:
    def __init__(self):
        self.calls = []

    async def save_prepared_inline_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(
            id="prepared-message-id",
            expiration_date=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )


def archived_video_row(**overrides):
    row = {
        "platform": "tiktok",
        "platform_post_id": "1234567890",
        "media_type": "video",
        "storage_key": "private/object",
        "content_type": "video/mp4",
        "thumbnail_url": "https://thumbnail.invalid/poster.jpg",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_owned_archived_video_creates_a_server_only_prepared_message(monkeypatch):
    repo = OwnedRepo(archived_video_row())
    storage = SigningStorage()
    bot = PreparedMessageBot()
    monkeypatch.setattr(api, "_repo", repo)
    monkeypatch.setattr(api, "_r2", storage)
    monkeypatch.setattr(api, "_share_bot", bot)
    monkeypatch.setattr(api.time, "time", lambda: 1_000)

    response = await api.profile_archive_share(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 99},
    )

    assert response.model_dump() == {
        "available": True,
        "prepared_message_id": "prepared-message-id",
        "expires_at": 1_000 + api.settings.archive_presign_seconds,
    }
    assert repo.calls == [("tenant", PROFILE, POST)]
    assert storage.calls == [("private/object", api.settings.archive_presign_seconds)]
    assert len(bot.calls) == 1
    args, kwargs = bot.calls[0]
    assert args[0] == 99
    result = args[1]
    assert result.type == "video"
    assert result.mime_type == "video/mp4"
    assert result.title == "Archived TikTok video"
    assert result.thumbnail_url == "https://thumbnail.invalid/poster.jpg"
    assert result.video_url == "https://signed.invalid/temporary"
    assert kwargs == {
        "allow_user_chats": True,
        "allow_bot_chats": False,
        "allow_group_chats": True,
        "allow_channel_chats": True,
    }


@pytest.mark.asyncio
async def test_share_expiry_never_exceeds_either_telegram_or_r2_capability(monkeypatch):
    class SoonExpiringBot(PreparedMessageBot):
        async def save_prepared_inline_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return SimpleNamespace(
                id="prepared-message-id",
                expiration_date=datetime.fromtimestamp(1_100, tz=timezone.utc),
            )

    monkeypatch.setattr(api, "_repo", OwnedRepo(archived_video_row()))
    monkeypatch.setattr(api, "_r2", SigningStorage())
    monkeypatch.setattr(api, "_share_bot", SoonExpiringBot())
    monkeypatch.setattr(api.time, "time", lambda: 1_000)

    response = await api.profile_archive_share(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 99},
    )

    assert response.expires_at == 1_100


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    archived_video_row(storage_key=None),
    archived_video_row(media_type="photo"),
    archived_video_row(content_type="video/webm"),
    archived_video_row(thumbnail_url=None),
    archived_video_row(thumbnail_url="http://thumbnail.invalid/poster.jpg"),
    archived_video_row(thumbnail_url="https://thumbnail.invalid/poster.png"),
    archived_video_row(thumbnail_url="https://thumbnail.invalid/poster.webp"),
])
async def test_unarchived_or_noncompliant_video_does_not_contact_storage_or_telegram(monkeypatch, row):
    storage = SigningStorage()
    bot = PreparedMessageBot()
    monkeypatch.setattr(api, "_repo", OwnedRepo(row))
    monkeypatch.setattr(api, "_r2", storage)
    monkeypatch.setattr(api, "_share_bot", bot)

    response = await api.profile_archive_share(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 99},
    )

    assert response.model_dump() == {
        "available": False,
        "prepared_message_id": None,
        "expires_at": None,
    }
    assert storage.calls == []
    assert bot.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_id,post_id", [
    ("not-a-uuid", str(POST)),
    (str(PROFILE), "not-a-uuid"),
    (str(PROFILE), str(POST)),
])
async def test_share_hides_malformed_and_foreign_resources(monkeypatch, profile_id, post_id):
    storage = SigningStorage()
    bot = PreparedMessageBot()
    monkeypatch.setattr(api, "_repo", OwnedRepo(None))
    monkeypatch.setattr(api, "_r2", storage)
    monkeypatch.setattr(api, "_share_bot", bot)

    with pytest.raises(HTTPException) as error:
        await api.profile_archive_share(
            profile_id, post_id, identity={"app_user_id": "other", "telegram_user_id": 99},
        )
    assert error.value.status_code == 404
    assert error.value.detail == "profile_archive_not_found"
    assert storage.calls == []
    assert bot.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["storage", "telegram"])
async def test_share_failures_are_sanitized(monkeypatch, broken):
    class BrokenStorage:
        async def presigned_get(self, *_):
            raise RuntimeError("private storage detail")

    class BrokenBot:
        async def save_prepared_inline_message(self, *_args, **_kwargs):
            raise RuntimeError("telegram response with private detail")

    monkeypatch.setattr(api, "_repo", OwnedRepo(archived_video_row()))
    monkeypatch.setattr(api, "_r2", BrokenStorage() if broken == "storage" else SigningStorage())
    monkeypatch.setattr(api, "_share_bot", BrokenBot() if broken == "telegram" else PreparedMessageBot())

    with pytest.raises(HTTPException) as error:
        await api.profile_archive_share(
            str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 99},
        )
    assert error.value.status_code == 503
    assert error.value.detail == "archive_share_temporarily_unavailable"
    assert "private" not in error.value.detail


@pytest.mark.parametrize("thumbnail", [
    "https://thumbnail.invalid/poster.jpg",
    "https://thumbnail.invalid/poster.JPEG?version=1",
    "https://thumbnail.invalid/opaque-thumbnail?format=jpeg",
])
def test_telegram_thumbnail_candidate_accepts_https_jpeg_urls_without_filename_suffix(thumbnail):
    assert api._is_telegram_thumbnail_candidate(thumbnail)
