"""Worker-owned public TikTok profile validation for the Add Profile flow."""

import asyncio
from dataclasses import dataclass

from providers.base import SourceUnavailable, UnsupportedUrl
from providers.tiktok.provider import normalize_tiktok_profile_target
from providers.tiktok.scanner import TikTokProfilePrivate, TikTokProfileScanner


class TikTokProfileValidationFailure(Exception):
    """A browser-safe validation category; raw provider causes stay internal."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TikTokProfileValidation:
    canonical_target: str
    platform_account_id: str
    username: str
    display_name: str | None
    avatar_url: str | None

    def safe_preview(self) -> dict[str, str | None]:
        return {
            "platform": "tiktok",
            "username": self.username,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
        }


class TikTokProfileValidator:
    """Uses the scanner's identity path only; it does not enumerate posts."""

    def __init__(self, scanner: TikTokProfileScanner | None = None):
        self.scanner = scanner or TikTokProfileScanner()

    async def validate(self, target: str) -> TikTokProfileValidation:
        try:
            username, canonical_target = normalize_tiktok_profile_target(target)
        except UnsupportedUrl as exc:
            raise TikTokProfileValidationFailure("invalid_target") from exc
        try:
            profile = await asyncio.to_thread(self.scanner.resolve_profile, username)
        except TikTokProfilePrivate as exc:
            raise TikTokProfileValidationFailure("profile_private") from exc
        except SourceUnavailable as exc:
            raise TikTokProfileValidationFailure("temporarily_unavailable") from exc
        except Exception as exc:
            raise TikTokProfileValidationFailure("temporarily_unavailable") from exc
        return TikTokProfileValidation(
            canonical_target=canonical_target,
            platform_account_id=profile.user_id,
            username=profile.username,
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
        )
