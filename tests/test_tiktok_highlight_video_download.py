import asyncio
import hashlib
import ssl
from uuid import UUID

import pytest

from apps.worker.processors.highlight_story_media import process_archive_highlight_story_media
from providers.base import HighlightStoryMediaDescriptor, MediaNotFound, SourceUnavailable
from providers.tiktok.highlight_media_download import TikTokHighlightMediaDownloader
from providers.tiktok.highlight_video_download import TikTokHighlightMp4Downloader


def _mp4_bytes():
    # Minimal structurally valid ISO-BMFF fixture: ftyp, moov, and non-empty mdat.
    return (
        b"\x00\x00\x00\x10ftypisom\x00\x00\x02\x00"
        b"\x00\x00\x00\x08moov"
        b"\x00\x00\x00\x0cmdatdata"
    )


class _Writer:
    def __init__(self, peer):
        self.peer = peer
        self.closed = False

    def get_extra_info(self, name):
        return self.peer if name == "peername" else None

    def write(self, _):
        pass

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def _reader(payload):
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


def _http(payload):
    return b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload


@pytest.mark.asyncio
async def test_highlight_mp4_downloader_streams_validated_bytes_and_hashes(tmp_path):
    video = _mp4_bytes()

    async def resolve(_, __):
        return ("8.8.8.8",)

    async def connect(*_, **kwargs):
        assert kwargs["server_hostname"] == "cdn.example.invalid"
        assert isinstance(kwargs["ssl"], ssl.SSLContext)
        assert kwargs["ssl"].check_hostname
        return _reader(_http(video)), _Writer(("8.8.8.8", 443))

    result = await TikTokHighlightMp4Downloader(resolve=resolve, open_connection=connect).download_asset(
        ("https://cdn.example.invalid/story",), tmp_path / "story.asset",
    )

    assert result.path.suffix == ".mp4"
    assert result.path.read_bytes() == video
    assert result.size_bytes == len(video)
    assert result.sha256 == hashlib.sha256(video).hexdigest()
    assert result.content_type == "video/mp4"


@pytest.mark.asyncio
async def test_highlight_mp4_rejects_html_invalid_boxes_and_cleans_temp_files(tmp_path):
    async def resolve(_, __):
        return ("8.8.8.8",)

    async def connect(*_, **__):
        return _reader(_http(b"<html>not a video</html>")), _Writer(("8.8.8.8", 443))

    downloader = TikTokHighlightMp4Downloader(resolve=resolve, open_connection=connect)
    with pytest.raises(MediaNotFound, match="TikTok Highlight video validation failed"):
        await downloader.download_asset(("https://cdn.example.invalid/story",), tmp_path / "story.asset")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_highlight_mp4_candidate_fallback_and_size_limit(tmp_path):
    video = _mp4_bytes()
    attempted = []

    async def resolve(host, _):
        attempted.append(host)
        return ("8.8.8.8",) if host.startswith("first") else ("1.1.1.1",)

    async def connect(ip, *_args, **_kwargs):
        if ip == "8.8.8.8":
            return _reader(_http(b"{}")), _Writer((ip, 443))
        return _reader(_http(video)), _Writer((ip, 443))

    downloader = TikTokHighlightMp4Downloader(resolve=resolve, open_connection=connect)
    result = await downloader.download_asset(
        ("https://first.example.invalid/story", "https://second.example.invalid/story"),
        tmp_path / "story.asset",
    )
    assert attempted == ["first.example.invalid", "second.example.invalid"]
    assert result.path.read_bytes() == video

    too_small = TikTokHighlightMp4Downloader(max_bytes=len(video) - 1, resolve=resolve, open_connection=connect)
    with pytest.raises(MediaNotFound):
        await too_small.download_asset(("https://second.example.invalid/story",), tmp_path / "too-small.asset")
    assert not (tmp_path / "too-small.asset").exists()


@pytest.mark.asyncio
async def test_highlight_mp4_rejects_private_dns_peer_and_unsafe_redirect(tmp_path):
    async def private_resolve(_, __):
        return ("127.0.0.1",)

    opened = False

    async def never_connect(*_, **__):
        nonlocal opened
        opened = True
        raise AssertionError("private address must not be connected")

    private = TikTokHighlightMp4Downloader(resolve=private_resolve, open_connection=never_connect)
    with pytest.raises(MediaNotFound, match="unsafe"):
        await private.download_asset(("https://cdn.example.invalid/story",), tmp_path / "private.asset")
    assert not opened

    async def public_resolve(_, __):
        return ("8.8.8.8",)

    async def redirect(*_, **__):
        return _reader(
            b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/private\r\nContent-Length: 0\r\n\r\n",
        ), _Writer(("8.8.8.8", 443))

    redirected = TikTokHighlightMp4Downloader(resolve=public_resolve, open_connection=redirect)
    with pytest.raises(MediaNotFound, match="unsafe"):
        await redirected.download_asset(("https://cdn.example.invalid/story",), tmp_path / "redirect.asset")

    async def rebinding(*_, **__):
        return _reader(_http(_mp4_bytes())), _Writer(("127.0.0.1", 443))

    rebinding_downloader = TikTokHighlightMp4Downloader(resolve=public_resolve, open_connection=rebinding)
    with pytest.raises(MediaNotFound, match="unsafe"):
        await rebinding_downloader.download_asset(("https://cdn.example.invalid/story",), tmp_path / "rebind.asset")


class _SlowChunkedReader:
    async def readuntil(self, _):
        return b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"

    async def readline(self):
        return b"1\r\n"

    async def readexactly(self, size):
        await asyncio.sleep(0.01)
        return b"x" if size == 1 else b"\r\n"


@pytest.mark.asyncio
async def test_highlight_mp4_candidate_deadline_and_truncated_framing_are_bounded(tmp_path):
    async def resolve(_, __):
        return ("8.8.8.8",)

    async def slow_connect(*_, **__):
        return _SlowChunkedReader(), _Writer(("8.8.8.8", 443))

    slow = TikTokHighlightMp4Downloader(
        resolve=resolve, open_connection=slow_connect, timeout_seconds=1, candidate_deadline_seconds=0.04,
    )
    with pytest.raises(SourceUnavailable, match="TikTok Highlight video download failed"):
        await slow.download_asset(("https://cdn.example.invalid/story",), tmp_path / "slow.asset")
    assert not list(tmp_path.iterdir())

    async def truncated_connect(*_, **__):
        return _reader(b"HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\nshort"), _Writer(("8.8.8.8", 443))

    truncated = TikTokHighlightMp4Downloader(resolve=resolve, open_connection=truncated_connect)
    with pytest.raises(SourceUnavailable):
        await truncated.download_asset(("https://cdn.example.invalid/story",), tmp_path / "truncated.asset")
    assert not list(tmp_path.iterdir())

    async def malformed_connect(*_, **__):
        return _reader(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
            b"Content-Length: 1\r\n\r\n1\r\nx\r\n0\r\n\r\n",
        ), _Writer(("8.8.8.8", 443))

    malformed = TikTokHighlightMp4Downloader(resolve=resolve, open_connection=malformed_connect)
    with pytest.raises(MediaNotFound):
        await malformed.download_asset(("https://cdn.example.invalid/story",), tmp_path / "malformed.asset")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_highlight_dispatcher_and_worker_persist_verified_mp4(tmp_path):
    video = _mp4_bytes()

    class Transport:
        async def download(self, candidates, destination):
            assert candidates == ("https://ephemeral.invalid/video",)
            destination.write_bytes(video)
            return TikTokHighlightMp4Downloader._validate_and_move(destination, destination, 1024)

    # The dispatcher accepts the dedicated MP4 seam and the worker receives
    # only DownloadedAsset metadata for the existing SHA/R2 persistence path.
    dispatcher = TikTokHighlightMediaDownloader(video_downloader=Transport())
    descriptor = HighlightStoryMediaDescriptor(0, "video", ("https://ephemeral.invalid/video",))
    downloaded = await dispatcher.download(descriptor, tmp_path / "story.asset")
    assert downloaded.content_type == "video/mp4"

    asset_id = UUID("33333333-3333-3333-3333-333333333333")

    class Repo:
        async def get_owned_highlight_story_media_target(self, *_):
            return {"username": "owner", "provider_namespace": "synthetic", "provider_collection_id": "c", "provider_item_id": "story"}

        async def upsert_owned_highlight_story_media_assets(self, *_):
            return {"inserted": 1, "refreshed": 0}

        async def list_owned_highlight_story_media_assets(self, *_):
            return [{"id": asset_id, "position": 0, "asset_type": "video"}]

        async def update_job_progress(self, *_):
            pass

    persisted = []

    class Provider:
        async def resolve_story_media(self, **_):
            return (descriptor,)

    class Service:
        async def persist(self, **kwargs):
            persisted.append(kwargs["downloaded"])
            return False

    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": "11111111-1111-1111-1111-111111111111", "membership_id": "22222222-2222-2222-2222-222222222222"}},
        Repo(), Provider(), dispatcher, Service(), execution_enabled=True,
    )
    assert result["outcome"] == "complete" and result["attached"] == 1
    assert persisted[0].content_type == "video/mp4"
