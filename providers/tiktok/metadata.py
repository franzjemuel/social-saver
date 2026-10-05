import asyncio
from typing import Any, Callable

from providers.base import SourceUnavailable


class TikTokPostMetadataResolver:
    """Use yt-dlp only for one concrete discovered TikTok post at a time."""

    def __init__(self, extract_info: Callable[[str], dict[str, Any]] | None = None):
        self._extract_info = extract_info or self._extract_with_ytdlp

    async def resolve(self, canonical_url: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._extract_info, canonical_url)

    @staticmethod
    def _extract_with_ytdlp(canonical_url: str) -> dict[str, Any]:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise SourceUnavailable("yt-dlp is not installed") from exc
        try:
            with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as downloader:
                result = downloader.extract_info(canonical_url, download=False)
        except Exception as exc:
            raise SourceUnavailable(f"TikTok post metadata failed: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise SourceUnavailable("TikTok post metadata returned invalid metadata")
        return result
