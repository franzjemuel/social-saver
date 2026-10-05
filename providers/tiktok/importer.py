from dataclasses import dataclass

from providers.tiktok.provider import DEVELOPMENT_MAX_POSTS, TikTokProfileProvider


@dataclass(frozen=True)
class ProfileImportSummary:
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

    async def import_profile(self, user_id, target: str, *, limit: int = DEVELOPMENT_MAX_POSTS) -> ProfileImportSummary:
        profile, posts = await self.provider.discover_profile(target, limit=limit)
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
            platform_account_id=profile.platform_account_id,
            posts_discovered=len(posts),
            posts_imported=imported,
            posts_skipped=skipped,
            failures=failures,
        )
