"""Public TikTok profile discovery backed by the pinned tt-dlp library."""

from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from providers.base import SourceUnavailable, TerminalProviderError


class TikTokProfilePrivate(TerminalProviderError):
    """The public profile scanner confirmed that this account is private."""


@dataclass(frozen=True)
class TikTokScannedProfile:
    username: str
    user_id: str
    sec_uid: str
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class TikTokScannedPost:
    post_id: str
    canonical_url: str
    is_photo: bool
    description: str | None = None


@dataclass(frozen=True)
class TikTokProfileScan:
    profile: TikTokScannedProfile
    posts: list[TikTokScannedPost]


class TikTokProfileScanner:
    """Discover public profile identity and ordered posts without downloading bytes."""

    def __init__(self, client_factory: Callable[[], object] | None = None):
        self._client_factory = client_factory or self._default_client

    @staticmethod
    def _default_client():
        try:
            from tt_dlp.client import TikTokClient
            from tt_dlp.models import Settings
        except ImportError as exc:
            raise SourceUnavailable("tt-dlp is not installed") from exc
        settings = Settings(
            output=Path("/tmp/social-saver-tiktok-unused"), cookies=None,
            profile_store=None, limit=0, sleep=0, overwrite=False,
            dry_run=True, stories=False, identify=False,
        )
        return TikTokClient(settings)

    def scan(self, username: str) -> TikTokProfileScan:
        try:
            client = self._client_factory()
            creator, identity, profile = self._resolve_creator(client, username)
            recent = creator.get("videoList") or ()
            profile_url = f"https://www.tiktok.com/@{identity.username}"
            items = client.collect_posts(
                identity.sec_uid, profile_url=profile_url, recent=recent, is_private=False,
            )
        except TikTokProfilePrivate:
            raise
        except SourceUnavailable:
            raise
        except Exception as exc:
            raise SourceUnavailable(f"TikTok profile discovery failed: {type(exc).__name__}") from exc

        posts = [
            TikTokScannedPost(
                post_id=str(item.post_id),
                canonical_url=(
                    f"https://www.tiktok.com/@{identity.username}/"
                    f"{'photo' if item.is_photo else 'video'}/{item.post_id}"
                ),
                is_photo=bool(item.is_photo),
                description=str(item.description) if item.description else None,
            )
            for item in items
        ]
        return TikTokProfileScan(profile=profile, posts=posts)

    def resolve_profile(self, username: str) -> TikTokScannedProfile:
        """Resolve only public profile identity/preview data; do not enumerate posts."""
        try:
            client = self._client_factory()
            _, _, profile = self._resolve_creator(client, username)
            return profile
        except TikTokProfilePrivate:
            raise
        except SourceUnavailable:
            raise
        except Exception as exc:
            raise SourceUnavailable(f"TikTok profile discovery failed: {type(exc).__name__}") from exc

    @staticmethod
    def _safe_http_url(value) -> str | None:
        if not isinstance(value, str):
            return None
        parsed = urlparse(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else None

    def _resolve_creator(self, client, username: str):
        with redirect_stdout(StringIO()):
            creator = client.creator_data(username)
            user_info = creator.get("userInfo") if isinstance(creator, dict) else None
            if not isinstance(user_info, dict):
                raise SourceUnavailable("TikTok creator discovery returned no user metadata")
            if user_info.get("code") not in (None, 0, 200):
                raise SourceUnavailable("TikTok creator discovery was rejected")
            user = user_info.get("user") if isinstance(user_info.get("user"), dict) else {}
            if user_info.get("privateAccount") or user.get("privateAccount"):
                raise TikTokProfilePrivate("TikTok profile is private")
            identity = client.identity_from_creator(creator)
            recent = creator.get("videoList") or ()
            if not identity:
                raise SourceUnavailable("TikTok creator discovery returned no identity")
            if not identity.sec_uid and recent:
                seed_id = str(recent[0].get("id") or "") if isinstance(recent[0], dict) else ""
                if seed_id:
                    seed = client.media_from_embed(seed_id)
                    if seed.author:
                        identity = seed.author
            if not identity.user_id or not identity.sec_uid:
                raise SourceUnavailable("TikTok creator discovery returned incomplete stable identity")
        profile = TikTokScannedProfile(
            username=identity.username,
            user_id=identity.user_id,
            sec_uid=identity.sec_uid,
            display_name=user.get("nickname") if isinstance(user.get("nickname"), str) else None,
            avatar_url=self._safe_http_url(
                user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb"),
            ),
        )
        return creator, identity, profile
