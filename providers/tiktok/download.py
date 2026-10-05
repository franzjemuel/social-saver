"""Worker-only TikTok video acquisition using the pinned tt-dlp library."""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Callable

from core.media_download import DownloadedAsset
from providers.base import SourceUnavailable


class _InvalidVideoResponse(Exception):
    """A response was not a usable MP4 video."""


class TikTokMediaDownloader:
    """Download exactly one concrete public TikTok video without cookies or state."""

    def __init__(self, client_factory: Callable | None = None):
        self._client_factory = client_factory or self._build_client

    async def download_post(self, canonical_url: str, destination: Path):
        """Resolve and acquire one video off the event loop into ``destination``."""
        return await asyncio.to_thread(self._download_sync, canonical_url, destination)

    @staticmethod
    def _build_client(output: Path):
        from tt_dlp.client import TikTokClient
        from tt_dlp.models import Settings

        # tt-dlp receives no cookie file and no profile store. Its output setting is
        # unused here but must be a caller-controlled temporary directory.
        settings = Settings(
            output=output,
            cookies=None,
            profile_store=None,
            limit=0,
            sleep=0,
            overwrite=True,
            dry_run=False,
            stories=False,
            identify=False,
        )
        return TikTokClient(settings)

    def _download_sync(self, canonical_url: str, destination: Path) -> DownloadedAsset:
        try:
            from tt_dlp.targets import parse_target

            target = parse_target(canonical_url)
            if not target.post_id:
                raise ValueError("concrete TikTok post is required")
            client = self._client_factory(destination.parent)
            item = client.media_from_embed(target.post_id)
        except Exception as exc:
            raise SourceUnavailable("TikTok media resolve failed") from exc

        if item.is_photo or not item.video_urls:
            raise SourceUnavailable("TikTok media validation failed")

        temporary = destination.with_name(destination.name + ".part")
        validation_failed = False
        try:
            temporary.unlink(missing_ok=True)
            for source_url in item.video_urls:
                try:
                    return self._download_url(client, source_url, canonical_url, temporary, destination)
                except _InvalidVideoResponse:
                    validation_failed = True
                    temporary.unlink(missing_ok=True)
                except Exception:
                    temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)

        if validation_failed:
            raise SourceUnavailable("TikTok media validation failed")
        raise SourceUnavailable("TikTok media download failed")

    @staticmethod
    def _download_url(client, source_url: str, referer: str, temporary: Path, destination: Path) -> DownloadedAsset:
        digest = hashlib.sha256()
        size = 0
        with client.request(source_url, headers={"Referer": referer}, attempts=1) as response:
            content_type = TikTokMediaDownloader._content_type(response)
            first = response.read(16 * 1024)
            if not TikTokMediaDownloader._is_mp4(first, content_type):
                raise _InvalidVideoResponse()
            with temporary.open("wb") as output:
                output.write(first)
                digest.update(first)
                size += len(first)
                while chunk := response.read(256 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        if size == 0:
            raise _InvalidVideoResponse()
        os.replace(temporary, destination)
        return DownloadedAsset(destination, size, digest.hexdigest(), content_type)

    @staticmethod
    def _content_type(response) -> str | None:
        headers = response.headers
        if hasattr(headers, "get_content_type"):
            return headers.get_content_type().lower()
        value = headers.get("content-type") if hasattr(headers, "get") else None
        return value.lower() if isinstance(value, str) else None

    @staticmethod
    def _is_mp4(first: bytes, content_type: str | None) -> bool:
        stripped = first.lstrip().lower()
        if not first or (content_type and (content_type.startswith("text/") or content_type in {
            "application/json", "application/javascript", "application/xml",
        })):
            return False
        if stripped.startswith((b"<!doctype html", b"<html", b"{")):
            return False
        return len(first) >= 8 and first[4:8] == b"ftyp"
