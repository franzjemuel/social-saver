import asyncio
import re
from urllib.parse import urlparse

from providers.base import ArchivedPost, ArchivedProfile, ProfileArchiveProvider, SourceUnavailable, UnsupportedUrl
from providers.tiktok.metadata import TikTokPostMetadataResolver
from providers.tiktok.normalize import normalize_scanned_tiktok_post, normalize_tiktok_post
from providers.tiktok.scanner import TikTokProfileScanner
from providers.tiktok.constants import INITIAL_PROFILE_IMPORT_POST_LIMIT


USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
DEVELOPMENT_MAX_POSTS = INITIAL_PROFILE_IMPORT_POST_LIMIT


def normalize_tiktok_profile_target(target: str) -> tuple[str, str]:
    if not isinstance(target, str):
        raise UnsupportedUrl("Invalid TikTok profile target")
    value = target.strip()
    if not value:
        raise UnsupportedUrl("Invalid TikTok profile target")
    if value.startswith("@"):
        username = value[1:]
    elif "://" not in value:
        username = value
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise UnsupportedUrl("Invalid TikTok profile target")
        try:
            port = parsed.port
        except ValueError:
            raise UnsupportedUrl("Invalid TikTok profile target") from None
        if parsed.username or parsed.password or port is not None:
            raise UnsupportedUrl("Invalid TikTok profile target")
        if (parsed.hostname or "").lower() not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
            raise UnsupportedUrl("Invalid TikTok profile target")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1 or not parts[0].startswith("@"):
            raise UnsupportedUrl("Invalid TikTok profile target")
        username = parts[0][1:]
    if not USERNAME.fullmatch(username):
        raise UnsupportedUrl("Invalid TikTok username")
    return username, f"https://www.tiktok.com/@{username}"


class TikTokProfileProvider(ProfileArchiveProvider):
    """tt-dlp discovers profiles; yt-dlp enriches only discovered post URLs."""

    def __init__(self, scanner: TikTokProfileScanner | None = None, metadata_resolver: TikTokPostMetadataResolver | None = None):
        self.scanner = scanner or TikTokProfileScanner()
        self.metadata_resolver = metadata_resolver or TikTokPostMetadataResolver()

    async def discover_profile(self, target: str, *, limit: int = DEVELOPMENT_MAX_POSTS) -> tuple[ArchivedProfile, list[ArchivedPost]]:
        if not 1 <= limit <= DEVELOPMENT_MAX_POSTS:
            raise ValueError(f"TikTok profile imports must request 1-{DEVELOPMENT_MAX_POSTS} posts during development")
        username, _ = normalize_tiktok_profile_target(target)
        scan = await asyncio.to_thread(self.scanner.scan, username)
        profile = ArchivedProfile(
            platform="tiktok",
            platform_account_id=scan.profile.user_id,
            username=scan.profile.username,
            metadata={"sec_uid": scan.profile.sec_uid, "scanner": "tt-dlp"},
        )
        posts = []
        for scanned in scan.posts[:limit]:
            try:
                metadata = await self.metadata_resolver.resolve(scanned.canonical_url)
                posts.append(normalize_tiktok_post(
                    metadata, is_photo=scanned.is_photo,
                    canonical_url=scanned.canonical_url,
                ))
            except SourceUnavailable as exc:
                posts.append(normalize_scanned_tiktok_post(
                    scanned.post_id, scanned.canonical_url, is_photo=scanned.is_photo,
                    caption=scanned.description, error=type(exc).__name__,
                ))
        return profile, posts
