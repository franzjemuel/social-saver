import asyncio
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from providers.base import MediaNotFound
from providers.tiktok.image_download import TikTokImageDownloader


def _write_image(path: Path, image_format="JPEG"):
    Image.new("RGB", (2, 3), color=(1, 2, 3)).save(path, image_format)


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
