import asyncio
import re
from typing import Any, Callable

from providers.base import ArchivedPost, ArchivedProfile, ProfileArchiveProvider, SourceUnavailable, UnsupportedUrl
from providers.tiktok.normalize import normalize_tiktok_post, normalize_tiktok_profile


USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
DEVELOPMENT_MAX_POSTS = 12


def normalize_tiktok_profile_target(target: str) -> tuple[str, str]:
    value = target.strip().rstrip("/")
    if value.startswith("@"):
        username = value[1:]
    else:
        match = re.fullmatch(r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._]{1,30})(?:\?.*)?", value)
        if not match:
            raise UnsupportedUrl("Send a TikTok username such as @creator or a TikTok profile URL")
        username = match.group(1)
    if not USERNAME.fullmatch(username):
        raise UnsupportedUrl("Invalid TikTok username")
    return username, f"https://www.tiktok.com/@{username}"


class TikTokProfileProvider(ProfileArchiveProvider):
    """yt-dlp-backed discovery only; it never downloads media bytes."""

    def __init__(self, extract_info: Callable[[str, int], dict[str, Any]] | None = None):
        self._extract_info = extract_info or self._extract_with_ytdlp

    async def discover_profile(self, target: str, *, limit: int = DEVELOPMENT_MAX_POSTS) -> tuple[ArchivedProfile, list[ArchivedPost]]:
        if not 1 <= limit <= DEVELOPMENT_MAX_POSTS:
            raise ValueError(f"TikTok profile imports must request 1-{DEVELOPMENT_MAX_POSTS} posts during development")
        username, profile_url = normalize_tiktok_profile_target(target)
        result = await asyncio.to_thread(self._extract_info, profile_url, limit)
        entries = [entry for entry in (result.get("entries") or []) if isinstance(entry, dict)][:limit]
        profile_source = next((entry for entry in entries if entry.get("uploader_id") or entry.get("channel_id")), result)
        profile = normalize_tiktok_profile(profile_source, username)
        posts = [normalize_tiktok_post(entry) for entry in entries]
        return profile, posts

    @staticmethod
    def _extract_with_ytdlp(profile_url: str, limit: int) -> dict[str, Any]:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:  # defensive message for direct source use
            raise SourceUnavailable("yt-dlp is not installed") from exc
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "playlistend": limit,
        }
        try:
            with YoutubeDL(options) as downloader:
                result = downloader.extract_info(profile_url, download=False)
        except Exception as exc:
            raise SourceUnavailable(f"TikTok profile discovery failed: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise SourceUnavailable("TikTok profile discovery returned invalid metadata")
        return result
