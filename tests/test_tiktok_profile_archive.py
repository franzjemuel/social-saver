from datetime import datetime, timezone
import inspect

import pytest

from core.repository import Repository
from providers.base import ArchivedPost
from providers.tiktok.importer import TikTokProfileImporter
from providers.tiktok.normalize import normalize_tiktok_post, normalize_tiktok_profile
from providers.tiktok.provider import DEVELOPMENT_MAX_POSTS, TikTokProfileProvider


BENCHMARK_ENTRY = {
    "id": "7176363825556376859",
    "webpage_url": "https://www.tiktok.com/@aliachin11/video/7176363825556376859",
    "description": "sleep us?",
    "timestamp": 1670803200,
    "upload_date": "20221212",
    "uploader": "aliachin11",
    "uploader_id": "7055967621082039297",
    "view_count": 281,
    "like_count": 25,
    "comment_count": 1,
    "repost_count": 0,
    "duration": 14,
    "thumbnail": "https://example.invalid/thumb.jpg",
    "url": "https://example.invalid/video.mp4",
    "extractor": "TikTok",
}


def test_tiktok_normalization_preserves_stable_identity_caption_date_and_metrics():
    profile = normalize_tiktok_profile(BENCHMARK_ENTRY, "aliachin11")
    post = normalize_tiktok_post(BENCHMARK_ENTRY, observed_at=datetime(2026, 10, 4, tzinfo=timezone.utc))

    assert profile.platform == "tiktok"
    assert profile.platform_account_id == "7055967621082039297"
    assert post.caption == "sleep us?"
    assert post.published_at == datetime(2022, 12, 12, tzinfo=timezone.utc)
    assert post.engagement.view_count == 281
    assert post.engagement.like_count == 25
    assert post.engagement.comment_count == 1
    assert post.engagement.repost_count == 0


def test_tiktok_post_is_provider_neutral_and_has_media_assets():
    post = normalize_tiktok_post(BENCHMARK_ENTRY)

    assert isinstance(post, ArchivedPost)
    assert post.platform_post_id == "7176363825556376859"
    assert post.assets[0].asset_type == "video"
    assert post.assets[0].duration_seconds == 14.0


@pytest.mark.asyncio
async def test_profile_discovery_enforces_development_limit_before_network():
    calls = []

    def extract(url, limit):
        calls.append((url, limit))
        return {"entries": [BENCHMARK_ENTRY]}

    provider = TikTokProfileProvider(extract)
    with pytest.raises(ValueError):
        await provider.discover_profile("@aliachin11", limit=DEVELOPMENT_MAX_POSTS + 1)
    assert calls == []

    profile, posts = await provider.discover_profile("@aliachin11", limit=1)
    assert profile.platform_account_id == "7055967621082039297"
    assert len(posts) == 1
    assert calls == [("https://www.tiktok.com/@aliachin11", 1)]


class FakeProvider:
    async def discover_profile(self, target, *, limit):
        return normalize_tiktok_profile(BENCHMARK_ENTRY, "aliachin11"), [normalize_tiktok_post(BENCHMARK_ENTRY)]


class FakeRepository:
    def __init__(self):
        self.post_ids = set()

    async def upsert_archived_profile(self, user_id, profile):
        assert user_id == "tenant-a"
        return "profile-a"

    async def upsert_archived_post(self, user_id, profile_id, post):
        assert (user_id, profile_id) == ("tenant-a", "profile-a")
        created = post.platform_post_id not in self.post_ids
        self.post_ids.add(post.platform_post_id)
        return "post-a", created


@pytest.mark.asyncio
async def test_duplicate_profile_post_import_is_idempotent():
    repo = FakeRepository()
    importer = TikTokProfileImporter(repo, FakeProvider())

    first = await importer.import_profile("tenant-a", "@aliachin11", limit=1)
    retry = await importer.import_profile("tenant-a", "@aliachin11", limit=1)

    assert (first.posts_imported, first.posts_skipped, first.failures) == (1, 0, 0)
    assert (retry.posts_imported, retry.posts_skipped, retry.failures) == (0, 1, 0)


def test_profile_archive_repository_is_tenant_scoped_and_retains_removed_posts():
    upsert = inspect.getsource(Repository.upsert_archived_post).lower()
    mark_removed = inspect.getsource(Repository.mark_archived_post_not_present).lower()

    assert "archived_profiles where id=$1 and user_id=$2" in upsert
    assert "on conflict(archived_profile_id,platform_post_id)" in upsert
    assert "profile.user_id=$1" in mark_removed
    assert "is_present_on_original=false" in mark_removed
