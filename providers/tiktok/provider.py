import re

from providers.base import ArchivedPost, ArchivedProfile, ProfileArchiveProvider, SourceUnavailable, UnsupportedUrl
from providers.tiktok.metadata import TikTokPostMetadataResolver
from providers.tiktok.normalize import normalize_scanned_tiktok_post, normalize_tiktok_post
from providers.tiktok.scanner import TikTokProfileScanner


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
    """tt-dlp discovers profiles; yt-dlp enriches only discovered post URLs."""

    def __init__(self, scanner: TikTokProfileScanner | None = None, metadata_resolver: TikTokPostMetadataResolver | None = None):
        self.scanner = scanner or TikTokProfileScanner()
        self.metadata_resolver = metadata_resolver or TikTokPostMetadataResolver()

    async def discover_profile(self, target: str, *, limit: int = DEVELOPMENT_MAX_POSTS) -> tuple[ArchivedProfile, list[ArchivedPost]]:
        if not 1 <= limit <= DEVELOPMENT_MAX_POSTS:
            raise ValueError(f"TikTok profile imports must request 1-{DEVELOPMENT_MAX_POSTS} posts during development")
        username, _ = normalize_tiktok_profile_target(target)
        scan = self.scanner.scan(username)
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
                posts.append(normalize_tiktok_post(metadata))
            except SourceUnavailable as exc:
                posts.append(normalize_scanned_tiktok_post(
                    scanned.post_id, scanned.canonical_url, is_photo=scanned.is_photo,
                    caption=scanned.description, error=type(exc).__name__,
                ))
        return profile, posts
