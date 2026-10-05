"""Worker-only native TikTok video acquisition."""

import asyncio
import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Callable

from core.media_download import DownloadedAsset
from providers.base import MediaNotFound, SourceUnavailable, UnsupportedUrl


class TikTokMediaDownloader:
    """Download one concrete public TikTok video with yt-dlp's native path."""

    def __init__(
        self,
        native_download: Callable[[str, Path], None] | None = None,
        *,
        max_bytes: int = 1_000_000_000,
        socket_timeout_seconds: int = 30,
    ):
        self._max_bytes = max_bytes
        self._native_download = native_download or (
            lambda url, output: self._download_with_ytdlp(
                url, output, max_bytes=max_bytes, socket_timeout_seconds=socket_timeout_seconds,
            )
        )

    async def download_post(self, canonical_url: str, destination: Path) -> DownloadedAsset:
        """Run blocking provider work off-loop and normalize its output path."""
        return await asyncio.to_thread(self._download_sync, canonical_url, destination)

    def _download_sync(self, canonical_url: str, destination: Path) -> DownloadedAsset:
        self._validate_concrete_video_target(canonical_url)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="social-saver-tiktok-", dir=destination.parent) as temporary_root:
                root = Path(temporary_root)
                try:
                    self._native_download(canonical_url, root)
                except OSError:
                    raise SourceUnavailable("local temporary file failure") from None
                except Exception:
                    raise SourceUnavailable("TikTok native download failed") from None
                return self._normalize_download(root, destination, max_bytes=self._max_bytes)
        except (MediaNotFound, SourceUnavailable, UnsupportedUrl):
            raise
        except OSError:
            raise SourceUnavailable("local temporary file failure") from None

    @staticmethod
    def _validate_concrete_video_target(canonical_url: str) -> None:
        try:
            from tt_dlp.targets import parse_target

            target = parse_target(canonical_url)
        except Exception:
            raise UnsupportedUrl("TikTok target resolution failed") from None
        if not target.post_id:
            raise UnsupportedUrl("TikTok target resolution failed")
        if target.media_kind == "photo":
            raise MediaNotFound("TikTok produced no video")

    @staticmethod
    def _download_with_ytdlp(
        canonical_url: str,
        output: Path,
        *,
        max_bytes: int = 1_000_000_000,
        socket_timeout_seconds: int = 30,
    ) -> None:
        from yt_dlp import YoutubeDL

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "cachedir": False,
            "retries": 1,
            "fragment_retries": 1,
            "socket_timeout": socket_timeout_seconds,
            "max_filesize": max_bytes,
            "outtmpl": str(output / "video.%(ext)s"),
        }
        with YoutubeDL(options) as downloader:
            downloader.extract_info(canonical_url, download=True)

    @staticmethod
    def _normalize_download(root: Path, destination: Path, *, max_bytes: int) -> DownloadedAsset:
        candidates = [
            path for path in root.rglob("*")
            if path.is_file() and not path.name.endswith(".part")
        ]
        video_candidates = [path for path in candidates if TikTokMediaDownloader._is_mp4_file(path)]
        if not video_candidates:
            if candidates:
                raise MediaNotFound("TikTok media validation failed")
            raise MediaNotFound("TikTok produced no video")
        if len(video_candidates) != 1:
            raise SourceUnavailable("TikTok native download failed")
        source = video_candidates[0]
        temporary = destination.with_name(destination.name + ".part")
        try:
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as input_file, temporary.open("wb") as output_file:
                while chunk := input_file.read(256 * 1024):
                    if size + len(chunk) > max_bytes:
                        raise MediaNotFound("TikTok media exceeds archive size limit")
                    output_file.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size == 0:
                raise MediaNotFound("TikTok media validation failed")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        content_type = mimetypes.guess_type(destination.name)[0] or "video/mp4"
        return DownloadedAsset(destination, size, digest.hexdigest(), content_type)

    @staticmethod
    def _is_mp4_file(path: Path) -> bool:
        try:
            with path.open("rb") as file:
                first = file.read(32)
        except OSError:
            return False
        stripped = first.lstrip().lower()
        return (
            len(first) >= 8
            and first[4:8] == b"ftyp"
            and not stripped.startswith((b"<!doctype html", b"<html", b"{"))
        )
