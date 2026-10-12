"""Offline regression coverage for one-link TikTok MP4 -> Telegram quick-save."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from core.config import settings
from providers.base import UnsupportedUrl
from providers.tiktok.quick_save import (
    is_tiktok_link, normalize_tiktok_video_url, normalize_tiktok_quick_save_url,
    resolve_tiktok_quick_save_url,
)
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


# Regression for the TikTok app's real, native share URL supplied by the tester.
SHARE_URL = "https://www.tiktok.com/t/ZPLSLxk8n/"
SHARE_TARGET = "https://www.tiktok.com/@theyuh_lvsh/video/7661979122322640136"


@pytest.mark.parametrize("link,expected", [
    (SHARE_URL, SHARE_URL),
    ("https://tiktok.com/t/ZPLSLxk8n/?share=1", SHARE_URL),
    ("https://vm.tiktok.com/ZPLSLxk8n/", "https://vm.tiktok.com/ZPLSLxk8n/"),
    ("https://vt.tiktok.com/ZPLSLxk8n/?_t=123", "https://vt.tiktok.com/ZPLSLxk8n/"),
    (SHARE_TARGET + "?lang=en", SHARE_TARGET),
])
def test_quick_save_accepts_owned_short_links_without_network(link, expected):
    assert is_tiktok_link(link)
    assert normalize_tiktok_quick_save_url(link) == expected


@pytest.mark.parametrize("link", [
    "http://www.tiktok.com/t/ZPLSLxk8n/",
    "https://www.tiktok.com.evil.test/t/ZPLSLxk8n/",
    "https://www.tiktok.com:444/t/ZPLSLxk8n/",
    "https://user@www.tiktok.com/t/ZPLSLxk8n/",
    "https://www.tiktok.com/t/../@creator/video/7661979122322640136",
    "https://www.tiktok.com/t/?target=https://127.0.0.1",
    "https://vm.tiktok.com/../../admin",
    "https://www.tiktok.com/@creator/photo/7661979122322640136",
])
def test_quick_save_rejects_unsafe_or_nonvideo_links(link):
    with pytest.raises(UnsupportedUrl):
        normalize_tiktok_quick_save_url(link)


@pytest.mark.asyncio
async def test_actual_share_link_resolves_to_canonical_public_video_with_mocked_redirect():
    requests = []

    def route(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": SHARE_TARGET + "?_r=1&_t=share"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route), follow_redirects=False) as client:
        canonical = await resolve_tiktok_quick_save_url(SHARE_URL, client=client)
    assert canonical == SHARE_TARGET
    assert requests == [SHARE_URL]


@pytest.mark.asyncio
async def test_direct_video_requires_no_network_even_with_a_shortlink_resolver_client():
    def block_network(request):
        raise AssertionError("direct video URLs must not resolve via HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(block_network)) as client:
        assert await resolve_tiktok_quick_save_url(SHARE_TARGET, client=client) == SHARE_TARGET


@pytest.mark.asyncio
async def test_two_hop_short_link_stays_inside_tiktok_and_resolves():
    requests = []

    def route(request):
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(301, headers={"location": "https://vt.tiktok.com/ABCDEFGH/"})
        return httpx.Response(302, headers={"location": SHARE_TARGET + "?share_app=1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        assert await resolve_tiktok_quick_save_url(SHARE_URL, client=client) == SHARE_TARGET
    assert requests == [SHARE_URL, "https://vt.tiktok.com/ABCDEFGH/"]


@pytest.mark.asyncio
@pytest.mark.parametrize("location", [
    "http://www.tiktok.com/@theyuh_lvsh/video/7661979122322640136",
    "https://example.com/fake",
    "https://127.0.0.1/private",
    "https://www.tiktok.com.evil.test/fake",
    "https://username@www.tiktok.com/@theyuh_lvsh/video/7661979122322640136",
    "https://www.tiktok.com:8443/@theyuh_lvsh/video/7661979122322640136",
    "//localhost/internal",
    "https://www.tiktok.com/@theyuh_lvsh/photo/7661979122322640136",
])
async def test_untrusted_or_nonvideo_redirects_never_get_fetched(location):
    requested = []

    def route(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(UnsupportedUrl):
            await resolve_tiktok_quick_save_url(SHARE_URL, client=client)
    assert requested == [SHARE_URL]


@pytest.mark.asyncio
async def test_redirect_loop_is_bounded():
    requested = []

    def route(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": SHARE_URL})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(UnsupportedUrl, match="redirect limit"):
            await resolve_tiktok_quick_save_url(SHARE_URL, client=client)
    assert len(requested) == 4


@pytest.mark.asyncio
async def test_nonredirect_or_missing_location_fails_safely():
    for response in [httpx.Response(200, text="large html"), httpx.Response(302)]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(UnsupportedUrl):
                await resolve_tiktok_quick_save_url(SHARE_URL, client=client)


@pytest.mark.asyncio
async def test_short_share_worker_uses_canonical_video_and_native_telegram_delivery():
    calls = []

    async def resolver(value):
        calls.append(value)
        return SHARE_TARGET

    job = {**JOB, "input": {"url": SHARE_URL}}
    repo, downloader, delivery = FakeRepo(), FakeDownloader(), FakeDelivery()
    result = await process_tiktok_quick_save(
        job, repo, object(), downloader=downloader, delivery=delivery, resolver=resolver,
    )
    assert calls == [SHARE_URL]
    assert downloader.calls == [SHARE_TARGET]
    assert result["telegram_message_ids"] == [123]
    assert delivery.calls[0][1][0][0] == "video"


@pytest.mark.asyncio
async def test_short_share_unavailable_fails_before_download_and_claim():
    async def blocked(_):
        raise httpx.ConnectTimeout("sensitive provider detail")

    repo, downloader, delivery = FakeRepo(), FakeDownloader(), FakeDelivery()
    with pytest.raises(TikTokQuickSaveFailure) as error:
        await process_tiktok_quick_save(
            {**JOB, "input": {"url": SHARE_URL}},
            repo, object(), downloader=downloader, delivery=delivery, resolver=blocked,
        )
    assert "sensitive" not in str(error.value)
    assert repo.claims == [] and downloader.calls == [] and delivery.calls == []


def test_bot_routes_short_share_links_without_resolving_them_in_the_handler():
    import inspect
    import apps.bot.main

    source = inspect.getsource(apps.bot.main.main)
    assert "normalize_tiktok_quick_save_url(text)" in source
    assert '"tiktok_quick_save"' in source
    assert "resolve_tiktok_quick_save_url" not in source
