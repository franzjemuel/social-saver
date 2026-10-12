"""Offline regression coverage for one-link TikTok MP4 -> Telegram quick-save."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import settings
from providers.base import UnsupportedUrl
from providers.tiktok.quick_save import is_tiktok_link, normalize_tiktok_video_url
from apps.worker.processors.tiktok_quick_save import (
    TikTokQuickSaveFailure,
    TikTokQuickSaveUnconfirmed,
    process_tiktok_quick_save,
)


@pytest.mark.parametrize("url,expected", [
    ("https://www.tiktok.com/@creator/video/7176363825556376859",
     "https://www.tiktok.com/@creator/video/7176363825556376859"),
    ("https://m.tiktok.com/@creator/video/7176363825556376859?is_from_webapp=1",
     "https://www.tiktok.com/@creator/video/7176363825556376859"),
])
def test_valid_public_video_urls_are_canonical(url, expected):
    assert is_tiktok_link(url)
    assert normalize_tiktok_video_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://www.tiktok.com/@creator/video/7176363825556376859",
    "https://www.tiktok.com.evil.test/@creator/video/7176363825556376859",
    "https://evil.test/@creator/video/7176363825556376859",
    "https://www.tiktok.com:444/@creator/video/7176363825556376859",
    "https://username@www.tiktok.com/@creator/video/7176363825556376859",
    "https://www.tiktok.com/@creator/photo/7176363825556376859",
    "https://www.tiktok.com/@creator",
    "https://vm.tiktok.com/12345/",
    "https://127.0.0.1/@creator/video/7176363825556376859",
    "https://www.tiktok.com/@creator/video/abc",
    "https://www.tiktok.com/@creator/video/7176363825556376859#fragment",
])
def test_unsafe_or_unsupported_video_links_are_rejected(url):
    with pytest.raises(UnsupportedUrl):
        normalize_tiktok_video_url(url)


class FakeDownloader:
    def __init__(self, *, size=1024, failure=None):
        self.calls = []
        self.size = size
        self.failure = failure

    async def download_post(self, url, output):
        self.calls.append(url)
        if self.failure:
            raise self.failure
        output.write_bytes(b"valid-mocked-mp4")
        return SimpleNamespace(size_bytes=self.size, content_type="video/mp4")


class FakeRepo:
    def __init__(self, claimed=True):
        self.claimed = claimed
        self.claims = []

    async def claim_tiktok_quick_save_delivery(self, job_id, user_id):
        self.claims.append((job_id, user_id))
        return self.claimed


class FakeDelivery:
    def __init__(self, *, ambiguous=False):
        self.calls = []
        self.ambiguous = ambiguous

    async def send_files(self, chat, files, caption=None):
        self.calls.append((chat, [(kind, path.name, Path(path).read_bytes()) for kind, path in files], caption))
        if self.ambiguous:
            raise RuntimeError("secret Telegram payload")
        return [SimpleNamespace(message_id=123)]


JOB = {
    "id": "job-1",
    "user_id": "tenant",
    "telegram_chat_id": 90210,
    "input": {"url": "https://www.tiktok.com/@creator/video/7176363825556376859"},
}


@pytest.mark.asyncio
async def test_video_is_delivered_as_one_forwardable_native_telegram_file():
    repo, downloader, delivery = FakeRepo(), FakeDownloader(), FakeDelivery()
    result = await process_tiktok_quick_save(JOB, repo, object(), downloader=downloader, delivery=delivery)
    assert result["telegram_message_ids"] == [123]
    assert result["platform"] == "tiktok"
    assert downloader.calls == [JOB["input"]["url"]]
    assert repo.claims == [("job-1", "tenant")]
    assert delivery.calls[0][0] == 90210
    assert delivery.calls[0][1] == [("video", "video.mp4", b"valid-mocked-mp4")]
    assert "Social Saver" in delivery.calls[0][2]


@pytest.mark.asyncio
async def test_redelivery_after_claim_does_not_send_second_telegram_message():
    downloader, delivery = FakeDownloader(), FakeDelivery()
    with pytest.raises(TikTokQuickSaveUnconfirmed):
        await process_tiktok_quick_save(JOB, FakeRepo(claimed=False), object(),
                                        downloader=downloader, delivery=delivery)
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_ambiguous_send_does_not_leak_errors_or_retry():
    delivery = FakeDelivery(ambiguous=True)
    with pytest.raises(TikTokQuickSaveUnconfirmed) as error:
        await process_tiktok_quick_save(JOB, FakeRepo(), object(),
                                        downloader=FakeDownloader(), delivery=delivery)
    assert "secret" not in str(error.value)
    assert not error.value.notify_user
    assert len(delivery.calls) == 1


@pytest.mark.asyncio
async def test_oversized_media_rejected_before_delivery_claim():
    repo, delivery = FakeRepo(), FakeDelivery()
    with pytest.raises(TikTokQuickSaveFailure):
        await process_tiktok_quick_save(JOB, repo, object(),
            downloader=FakeDownloader(size=settings.telegram_hosted_upload_limit_bytes + 1),
            delivery=delivery)
    assert repo.claims == []
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_no_download_or_claim_for_tiktok_photo_url():
    repo, downloader, delivery = FakeRepo(), FakeDownloader(), FakeDelivery()
    photo_job = {**JOB, "input": {"url": "https://www.tiktok.com/@creator/photo/7176363825556376859"}}
    with pytest.raises(TikTokQuickSaveFailure):
        await process_tiktok_quick_save(photo_job, repo, object(), downloader=downloader, delivery=delivery)
    assert downloader.calls == []
    assert repo.claims == []
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_download_failure_is_sanitized_and_never_contacts_telegram():
    repo, delivery = FakeRepo(), FakeDelivery()
    with pytest.raises(TikTokQuickSaveFailure) as error:
        await process_tiktok_quick_save(JOB, repo, object(),
            downloader=FakeDownloader(failure=RuntimeError("secret download endpoint")), delivery=delivery)
    assert "secret" not in str(error.value)
    assert repo.claims == []
    assert delivery.calls == []
