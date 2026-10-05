from dataclasses import dataclass

from providers.tiktok.constants import INITIAL_PROFILE_IMPORT_POST_LIMIT
from providers.tiktok.provider import TikTokProfileProvider


class TikTokProfileChanged(Exception):
    """The username no longer resolves to the account the user validated."""


@dataclass(frozen=True)
class ProfileImportSummary:
    archived_profile_id: object
    platform_account_id: str
    posts_discovered: int
    posts_imported: int
    posts_skipped: int
    failures: int


class TikTokProfileImporter:
    """Persist normalized metadata. Media downloading remains a later worker concern."""

    def __init__(self, repo, provider: TikTokProfileProvider | None = None):
        self.repo = repo
        self.provider = provider or TikTokProfileProvider()

    async def import_profile(
        self, user_id, target: str, *, expected_platform_account_id: str | None = None,
        limit: int = INITIAL_PROFILE_IMPORT_POST_LIMIT,
    ) -> ProfileImportSummary:
        profile, posts = await self.provider.discover_profile(target, limit=limit)
        if expected_platform_account_id is not None and profile.platform_account_id != expected_platform_account_id:
            raise TikTokProfileChanged("TikTok profile identity changed")
        profile_id = await self.repo.upsert_archived_profile(user_id, profile)
        imported = skipped = failures = 0
        for post in posts:
            try:
                _, created = await self.repo.upsert_archived_post(user_id, profile_id, post)
            except Exception:
                failures += 1
                continue
            if created:
                imported += 1
            else:
                skipped += 1
        return ProfileImportSummary(
            archived_profile_id=profile_id,
            platform_account_id=profile.platform_account_id,
            posts_discovered=len(posts),
            posts_imported=imported,
            posts_skipped=skipped,
            failures=failures,
        )
