import asyncio
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from providers.base import MediaNotFound, SourceUnavailable
from providers.tiktok.image_download import TikTokImageDownloader


def _write_image(path: Path, image_format="JPEG"):
    Image.new("RGB", (2, 3), color=(1, 2, 3)).save(path, image_format)


def _jpeg_bytes():
    output = BytesIO()
    Image.new("RGB", (2, 3), color=(1, 2, 3)).save(output, "JPEG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_image_downloader_falls_back_and_validates_real_bytes(tmp_path, monkeypatch):
    calls = []

    async def fake_fetch(url, destination):
        calls.append(url)
        if len(calls) == 1:
            destination.write_bytes(b"<html>not an image</html>")
        else:
            _write_image(destination)

    downloader = TikTokImageDownloader()
    monkeypatch.setattr(downloader, "_fetch", fake_fetch)
    result = await downloader.download_asset(
        ("https://cdn.example.invalid/first", "https://cdn.example.invalid/second"),
        tmp_path / "asset.image",
    )
    assert calls == ["https://cdn.example.invalid/first", "https://cdn.example.invalid/second"]
    assert result.content_type == "image/jpeg"
    assert result.path.suffix == ".jpg"
    assert result.size_bytes == result.path.stat().st_size
    assert result.sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert not (tmp_path / "asset.image.candidate").exists()


@pytest.mark.asyncio
async def test_image_downloader_rejects_malformed_and_oversized_candidates(tmp_path, monkeypatch):
    async def malformed(_, destination):
        destination.write_bytes(b'{"error":"not image"}')

    downloader = TikTokImageDownloader(max_bytes=32)
    monkeypatch.setattr(downloader, "_fetch", malformed)
    with pytest.raises(MediaNotFound, match="TikTok image validation failed"):
        await downloader.download_asset(("https://cdn.example.invalid/image",), tmp_path / "asset.image")
    assert not list(tmp_path.iterdir())


def test_image_downloader_rejects_private_loopback_and_unsafe_urls():
    for url in (
        "http://cdn.example.invalid/image.jpg",
        "https://localhost/image.jpg",
        "https://127.0.0.1/image.jpg",
        "https://[::1]/image.jpg",
        "https://user@cdn.example.invalid/image.jpg",
        "https://cdn.example.invalid:444/image.jpg",
    ):
        with pytest.raises(MediaNotFound, match="unsafe"):
            TikTokImageDownloader._validate_url(url)


class _Writer:
    def __init__(self, peer): self.peer = peer
    def get_extra_info(self, name): return self.peer if name == "peername" else None
    def write(self, _): pass
    async def drain(self): pass
    def close(self): pass
    async def wait_closed(self): pass


def _reader(payload):
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_image_downloader_revalidates_redirects_and_pins_dns_peer(tmp_path):
    resolves = []

    async def resolve(host, _):
        resolves.append(host)
        return ("8.8.8.8",)

    async def redirecting_connection(*_, **__):
        return _reader(b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/private\r\n\r\n"), _Writer(("8.8.8.8", 443))

    downloader = TikTokImageDownloader(resolve=resolve, open_connection=redirecting_connection)
    with pytest.raises(MediaNotFound, match="unsafe"):
        await downloader._fetch("https://cdn.example.invalid/image.jpg", tmp_path / "image")
    assert resolves == ["cdn.example.invalid"]

    async def rebinding_connection(*_, **__):
        return _reader(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx"), _Writer(("127.0.0.1", 443))

    pinned = TikTokImageDownloader(resolve=resolve, open_connection=rebinding_connection)
    with pytest.raises(MediaNotFound, match="unsafe"):
        await pinned._fetch("https://cdn.example.invalid/image.jpg", tmp_path / "image")


@pytest.mark.asyncio
async def test_image_downloader_refuses_private_dns_before_connect(tmp_path):
    opened = False

    async def resolve(_, __): return ("127.0.0.1",)
    async def connect(*_, **__):
        nonlocal opened
        opened = True
        raise AssertionError("unsafe address must never be connected")

    downloader = TikTokImageDownloader(resolve=resolve, open_connection=connect)
    with pytest.raises(MediaNotFound, match="unsafe"):
        await downloader._fetch("https://cdn.example.invalid/image.jpg", tmp_path / "image")
    assert opened is False


class _SlowChunkedReader:
    """A response which keeps delivering legal small chunks forever."""

    async def readuntil(self, _):
        return b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"

    async def readline(self):
        return b"1\r\n"

    async def readexactly(self, size):
        await asyncio.sleep(0.01)
        return b"x" if size == 1 else b"\r\n"


@pytest.mark.asyncio
async def test_candidate_deadline_stops_a_continuous_slow_trickle_response(tmp_path):
    async def resolve(_, __): return ("8.8.8.8",)
    async def connect(*_, **__): return _SlowChunkedReader(), _Writer(("8.8.8.8", 443))

    downloader = TikTokImageDownloader(
        resolve=resolve, open_connection=connect, timeout_seconds=1, candidate_deadline_seconds=0.04,
    )
    with pytest.raises(SourceUnavailable, match="TikTok image download failed"):
        await downloader.download_asset(("https://cdn.example.invalid/image",), tmp_path / "image")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\n1\r\nx\r\n0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\nx",
    b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nxx",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;" + b"a" * 1024 + b"\r\nx\r\n0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\nContent-Length: 1\r\n\r\n",
])
async def test_image_downloader_rejects_ambiguous_or_malformed_http_framing(tmp_path, payload):
    async def resolve(_, __): return ("8.8.8.8",)
    async def connect(*_, **__): return _reader(payload), _Writer(("8.8.8.8", 443))

    downloader = TikTokImageDownloader(resolve=resolve, open_connection=connect)
    with pytest.raises((MediaNotFound, SourceUnavailable)) as error:
        await downloader.download_asset(("https://cdn.example.invalid/image",), tmp_path / "image")
    assert "https://" not in str(error.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "reason"), [
    (b"HTTP/1.1 200 OK\r\nBroken Header\r\n\r\n", "malformed_header"),
    (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
        b"Content-Length: 1\r\n\r\n1\r\nx\r\n0\r\n\r\n",
        "ambiguous_message_length",
    ),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nxx", "truncated_body"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\n0\r\n\r\n", "invalid_chunk_framing"),
])
async def test_image_downloader_logs_sanitized_framing_diagnostics(tmp_path, caplog, payload, reason):
    async def resolve(_, __):
        return ("8.8.8.8",)

    async def connect(*_, **__):
        return _reader(payload), _Writer(("8.8.8.8", 443))

    downloader = TikTokImageDownloader(resolve=resolve, open_connection=connect)
    candidate = "https://private-candidate.example.invalid/image"
    with pytest.raises((MediaNotFound, SourceUnavailable)) as error:
        await downloader.download_asset((candidate,), tmp_path / "opaque-asset.image")

    assert str(error.value) in {
        "TikTok image response is invalid",
        "TikTok image response failed",
        "TikTok image download failed",
    }
    assert f"framing_reason={reason}" in caplog.text
    assert "provider=tiktok" in caplog.text
    assert "candidate_attempt=1" in caplog.text
    assert "correlation_id=" in caplog.text
    assert candidate not in caplog.text
    assert "Content-Length" not in caplog.text
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_image_downloader_falls_back_after_invalid_http_framing(tmp_path):
    """A strict framing rejection consumes only that bounded candidate."""
    image = _jpeg_bytes()
    attempted = []

    async def resolve(host, _):
        attempted.append(host)
        return ("8.8.8.8",) if host == "first.example.invalid" else ("1.1.1.1",)

    async def connect(ip, *_args, **_kwargs):
        if ip == "8.8.8.8":
            # Duplicate Content-Length is deliberately rejected before image
            # validation; the next provider candidate must still be attempted.
            return _reader(
                b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n"
                b"Content-Length: 1\r\n\r\nx",
            ), _Writer((ip, 443))
        return _reader(
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(image)).encode()
            + b"\r\n\r\n"
            + image,
        ), _Writer((ip, 443))

    downloader = TikTokImageDownloader(resolve=resolve, open_connection=connect)
    result = await downloader.download_asset(
        ("https://first.example.invalid/image", "https://second.example.invalid/image"),
        tmp_path / "image",
    )

    assert attempted == ["first.example.invalid", "second.example.invalid"]
    assert result.content_type == "image/jpeg"
    assert result.path.read_bytes() == image
    assert not (tmp_path / "image.candidate").exists()


@pytest.mark.asyncio
async def test_image_downloader_requires_crlf_after_each_chunk_and_accepts_valid_chunked_jpeg(tmp_path):
    image = _jpeg_bytes()

    async def resolve(_, __): return ("8.8.8.8",)

    async def malformed_connect(*_, **__):
        payload = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{len(image):X}".encode() + b"\r\n" + image + b"\n\n0\r\n\r\n"
        )
        return _reader(payload), _Writer(("8.8.8.8", 443))

    malformed = TikTokImageDownloader(resolve=resolve, open_connection=malformed_connect)
    with pytest.raises(MediaNotFound, match="TikTok image response is invalid"):
        await malformed.download_asset(("https://cdn.example.invalid/image",), tmp_path / "malformed")

    async def valid_connect(*_, **__):
        payload = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{len(image):X}".encode() + b"\r\n" + image + b"\r\n0\r\n\r\n"
        )
        return _reader(payload), _Writer(("8.8.8.8", 443))

    valid = TikTokImageDownloader(resolve=resolve, open_connection=valid_connect)
    result = await valid.download_asset(("https://cdn.example.invalid/image",), tmp_path / "valid")
    assert result.content_type == "image/jpeg" and result.path.read_bytes() == image
