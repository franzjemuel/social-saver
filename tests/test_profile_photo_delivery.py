from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from apps.worker.processors.profile_photo_delivery import (
    ProfilePhotoDeliveryFailure,
    ProfilePhotoDeliveryUnconfirmed,
    process_deliver_profile_photos,
)
from core.repository import ProfileMediaJob
from core.telegram_delivery import TelegramDelivery
from apps.worker.main import complete_terminal_failure
from providers.base import TerminalProviderError


PROFILE = UUID("11111111-1111-1111-1111-111111111111")
POST = UUID("22222222-2222-2222-2222-222222222222")


class DeliveryRepo:
    def __init__(self, assets):
        self.assets = assets
        self.calls = []

    async def list_owned_complete_profile_photo_delivery_assets(self, user_id, profile_id, post_id):
        self.calls.append((user_id, profile_id, post_id))
        return self.assets

    async def claim_profile_photo_delivery_attempt(self, *_):
        return "claimed"

    async def confirm_profile_photo_delivery_attempt(self, *_):
        return True


class Storage:
    def __init__(self):
        self.keys = []

    async def download_file(self, key, path):
        self.keys.append(key)
        Path(path).write_bytes(b"image-bytes")


class Delivery:
    def __init__(self):
        self.calls = []

    async def send_files(self, chat_id, files):
        self.calls.append((chat_id, [(kind, path.name, path.read_bytes()) for kind, path in files]))


@pytest.mark.asyncio
async def test_owned_complete_photos_are_downloaded_in_position_order_and_delivered_to_verified_dm():
    assets = [
        {"position": 0, "storage_key": "private/first", "size_bytes": 5, "content_type": "image/jpeg"},
        {"position": 1, "storage_key": "private/second", "size_bytes": 5, "content_type": "image/png"},
    ]
    repo, storage, delivery = DeliveryRepo(assets), Storage(), Delivery()

    result = await process_deliver_profile_photos(
        {"id": "job", "user_id": "tenant", "telegram_chat_id": 123,
         "input": {"profile_id": str(PROFILE), "post_id": str(POST), "asset_count": 2}},
        repo, bot=object(), storage=storage, delivery=delivery,
    )

    assert result == {"sent": 2, "batches": 1}
    assert repo.calls == [("tenant", str(PROFILE), str(POST))]
    assert storage.keys == ["private/first", "private/second"]
    assert delivery.calls == [(123, [("photo", "00.jpg", b"image-bytes"), ("photo", "01.png", b"image-bytes")])]


@pytest.mark.asyncio
@pytest.mark.parametrize("assets", [
    [],
    [{"position": 0, "storage_key": "private/one", "size_bytes": 0, "content_type": "image/jpeg"}],
    [{"position": 0, "storage_key": "private/one", "size_bytes": 5, "content_type": "image/webp"}],
])
async def test_incomplete_or_unsupported_delivery_never_contacts_storage_or_telegram(assets):
    repo, storage, delivery = DeliveryRepo(assets), Storage(), Delivery()
    with pytest.raises(ProfilePhotoDeliveryFailure, match="Archived photos could not be sent"):
        await process_deliver_profile_photos(
            {"id": "job", "user_id": "tenant", "telegram_chat_id": 123,
             "input": {"profile_id": str(PROFILE), "post_id": str(POST), "asset_count": 1}},
            repo, bot=object(), storage=storage, delivery=delivery,
        )
    assert storage.keys == []
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_storage_or_telegram_failures_are_terminal_and_sanitized():
    assets = [{"position": 0, "storage_key": "private/secret", "size_bytes": 5, "content_type": "image/jpeg"}]

    class BrokenStorage(Storage):
        async def download_file(self, *_):
            raise RuntimeError("private storage key")

    with pytest.raises(ProfilePhotoDeliveryFailure) as error:
        await process_deliver_profile_photos(
            {"id": "job", "user_id": "tenant", "telegram_chat_id": 123,
             "input": {"profile_id": str(PROFILE), "post_id": str(POST), "asset_count": 1}},
            DeliveryRepo(assets), bot=object(), storage=BrokenStorage(), delivery=Delivery(),
        )
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_ambiguous_telegram_response_is_not_retried_or_reported_as_sent():
    assets = [{"position": 0, "storage_key": "private/one", "size_bytes": 5, "content_type": "image/jpeg"}]

    class AttemptRepo(DeliveryRepo):
        def __init__(self):
            super().__init__(assets)
            self.state = "new"

        async def claim_profile_photo_delivery_attempt(self, *_):
            if self.state == "new":
                self.state = "attempted"
                return "claimed"
            return self.state

    class AmbiguousDelivery(Delivery):
        async def send_files(self, chat_id, files):
            await super().send_files(chat_id, files)
            raise RuntimeError("provider response body with private fields")

    repo, storage, delivery = AttemptRepo(), Storage(), AmbiguousDelivery()
    job = {"id": "job", "user_id": "tenant", "telegram_chat_id": 123,
           "input": {"profile_id": str(PROFILE), "post_id": str(POST), "asset_count": 1}}
    with pytest.raises(ProfilePhotoDeliveryUnconfirmed) as error:
        await process_deliver_profile_photos(job, repo, bot=object(), storage=storage, delivery=delivery)
    assert "private" not in str(error.value)
    with pytest.raises(ProfilePhotoDeliveryUnconfirmed):
        await process_deliver_profile_photos(job, repo, bot=object(), storage=storage, delivery=delivery)
    assert len(delivery.calls) == 1


@pytest.mark.asyncio
async def test_post_send_crash_is_at_most_once_and_confirmed_attempt_can_finish_without_resend():
    assets = [{"position": 0, "storage_key": "private/one", "size_bytes": 5, "content_type": "image/jpeg"}]

    class CrashAfterSendRepo(DeliveryRepo):
        def __init__(self):
            super().__init__(assets)
            self.state = "new"

        async def claim_profile_photo_delivery_attempt(self, *_):
            if self.state == "new":
                self.state = "attempted"
                return "claimed"
            return self.state

        async def confirm_profile_photo_delivery_attempt(self, *_):
            raise SystemExit("synthetic worker crash after Telegram acceptance")

    repo, delivery = CrashAfterSendRepo(), Delivery()
    job = {"id": "job", "user_id": "tenant", "telegram_chat_id": 123,
           "input": {"profile_id": str(PROFILE), "post_id": str(POST), "asset_count": 1}}
    with pytest.raises(SystemExit):
        await process_deliver_profile_photos(job, repo, bot=object(), storage=Storage(), delivery=delivery)
    with pytest.raises(ProfilePhotoDeliveryUnconfirmed):
        await process_deliver_profile_photos(job, repo, bot=object(), storage=Storage(), delivery=delivery)
    assert len(delivery.calls) == 1

    class ConfirmedRepo(DeliveryRepo):
        async def claim_profile_photo_delivery_attempt(self, *_):
            return "confirmed"

    recovered = await process_deliver_profile_photos(
        job, ConfirmedRepo(assets), bot=object(), storage=Storage(), delivery=Delivery(),
    )
    assert recovered == {"sent": 1, "batches": 1, "recovered": True}


@pytest.mark.asyncio
async def test_terminal_error_notification_failure_does_not_prevent_queue_cleanup():
    calls = []

    class Repo:
        async def fail_job(self, *args):
            calls.append(("failed", args))

    class Queue:
        async def archive(self, message_id):
            calls.append(("archived", message_id))

    class BlockedBot:
        async def send_message(self, *_):
            raise RuntimeError("bot blocked")

    await complete_terminal_failure(
        Repo(), Queue(), BlockedBot(), {"msg_id": 99}, "job", {"telegram_chat_id": 123},
        TerminalProviderError("safe failure"),
    )
    assert calls[0][0] == "failed"
    assert calls[1] == ("archived", 99)


@pytest.mark.asyncio
async def test_photo_delivery_endpoint_is_owned_and_coalesces_duplicate_requests(monkeypatch):
    calls = []

    class Repo:
        async def create_owned_profile_photo_delivery_job(self, user, chat, profile, post, *, queue_name):
            calls.append((user, chat, profile, post, queue_name))
            return ProfileMediaJob(UUID("33333333-3333-3333-3333-333333333333"), False)

    monkeypatch.setattr(api, "_repo", Repo())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="media_jobs"))
    monkeypatch.setattr(api, "_entitlements", SimpleNamespace(plan_for=lambda _: _value("free")))
    monkeypatch.setattr(api, "_abuse", SimpleNamespace(check=lambda *_: _value(SimpleNamespace(allowed=True, reset=0))))
    result = await api.send_archived_photos_to_telegram(
        str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 123},
    )
    assert result.status == "queued"
    assert calls == [("tenant", 123, PROFILE, POST, "media_jobs")]

    class ForeignRepo:
        async def create_owned_profile_photo_delivery_job(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(api, "_repo", ForeignRepo())
    with pytest.raises(HTTPException) as error:
        await api.send_archived_photos_to_telegram(
            str(PROFILE), str(POST), identity={"app_user_id": "other", "telegram_user_id": 456},
        )
    assert (error.value.status_code, error.value.detail) == (404, "profile_archive_not_found")


async def _value(value):
    return value


@pytest.mark.asyncio
async def test_photo_delivery_is_rate_limited_before_queueing(monkeypatch):
    class Repo:
        async def create_owned_profile_photo_delivery_job(self, *_args, **_kwargs):
            raise AssertionError("rate limited requests must not queue")

    monkeypatch.setattr(api, "_repo", Repo())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="media_jobs"))
    monkeypatch.setattr(api, "_entitlements", SimpleNamespace(plan_for=lambda _: _value("free")))
    monkeypatch.setattr(api, "_abuse", SimpleNamespace(check=lambda *_: _value(SimpleNamespace(allowed=False, reset=10_000))))
    monkeypatch.setattr(api.time, "time", lambda: 9_999)
    with pytest.raises(HTTPException) as error:
        await api.send_archived_photos_to_telegram(
            str(PROFILE), str(POST), identity={"app_user_id": "tenant", "telegram_user_id": 123},
        )
    assert (error.value.status_code, error.value.detail) == (429, "rate_limited")


@pytest.mark.asyncio
async def test_telegram_delivery_uses_one_photo_or_an_ordered_album(tmp_path):
    class Bot:
        def __init__(self):
            self.photo = []
            self.groups = []

        async def send_photo(self, chat_id, upload, **kwargs):
            self.photo.append((chat_id, upload, kwargs))
            return object()

        async def send_media_group(self, chat_id, media):
            self.groups.append((chat_id, media))
            return [object() for _ in media]

    first, second = tmp_path / "first.jpg", tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    bot = Bot()
    await TelegramDelivery(bot).send_files(123, [("photo", first), ("photo", second)])
    assert bot.photo == []
    assert len(bot.groups) == 1
    assert [item.type for item in bot.groups[0][1]] == ["photo", "photo"]
