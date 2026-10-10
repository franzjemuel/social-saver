from datetime import datetime, timezone
import inspect
import threading

import pytest

from core.repository import Repository
from providers.base import ArchivedPost
from providers.tiktok.importer import TikTokProfileImporter
from providers.tiktok.normalize import normalize_scanned_tiktok_post, normalize_tiktok_post, normalize_tiktok_profile
from providers.base import SourceUnavailable
from providers.tiktok.provider import DEVELOPMENT_MAX_POSTS, TikTokProfileProvider
from providers.tiktok.scanner import (
    TikTokProfileScan,
    TikTokProfileScanner,
    TikTokScannedPost,
    TikTokScannedProfile,
)


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


def test_photo_normalization_preserves_single_image_and_ordered_carousel_candidates():
    single = normalize_tiktok_post(
        BENCHMARK_ENTRY,
        is_photo=True,
        photo_candidate_groups=(("https://images.example.invalid/one.jpg",),),
    )
    carousel = normalize_tiktok_post(
        BENCHMARK_ENTRY,
        is_photo=True,
        photo_candidate_groups=(
            ("https://images.example.invalid/0.jpg", "https://fallback.example.invalid/0.jpg"),
            ("https://images.example.invalid/1.jpg",),
            ("https://images.example.invalid/2.jpg",),
        ),
    )

    assert single.media_type == "photo"
    assert [(asset.position, asset.asset_type) for asset in single.assets] == [(0, "photo")]
    assert carousel.media_type == "carousel"
    assert [asset.position for asset in carousel.assets] == [0, 1, 2]
    assert carousel.assets[0].source_url == "https://images.example.invalid/0.jpg"
    assert carousel.assets[0].metadata == {
        "fallback_source_urls": ["https://fallback.example.invalid/0.jpg"],
    }
    assert carousel.metadata["image_count"] == 3


def test_scanned_photo_fallback_preserves_assets_without_rich_metadata():
    post = normalize_scanned_tiktok_post(
        "post", "https://www.tiktok.com/@creator/photo/post",
        is_photo=True,
        caption="scanner caption",
        error="SourceUnavailable",
        photo_candidate_groups=(
            ("https://images.example.invalid/0.jpg",),
            (),
            ("https://images.example.invalid/2.jpg",),
        ),
    )

    assert post.media_type == "carousel"
    assert [asset.position for asset in post.assets] == [0, 2]
    assert post.caption == "scanner caption"
    assert post.metadata["metadata_resolution_error"] == "SourceUnavailable"


@pytest.mark.asyncio
async def test_profile_discovery_enforces_development_limit_after_scanning():
    scanner = FakeScanner(post_count=13)
    resolver = FakeResolver()
    provider = TikTokProfileProvider(scanner, resolver)
    with pytest.raises(ValueError):
        await provider.discover_profile("@aliachin11", limit=DEVELOPMENT_MAX_POSTS + 1)
    assert scanner.calls == []

    profile, posts = await provider.discover_profile("@aliachin11", limit=DEVELOPMENT_MAX_POSTS)
    assert profile.platform_account_id == "7055967621082039297"
    assert profile.metadata["sec_uid"] == "MS4wLjABAAAAbenchmarkSecUidValue"
    assert len(posts) == DEVELOPMENT_MAX_POSTS
    assert scanner.calls == ["aliachin11"]
    assert scanner.thread_ids[0] != threading.get_ident()
    assert len(resolver.urls) == DEVELOPMENT_MAX_POSTS
    assert all("/@aliachin11/video/" in url for url in resolver.urls)


class FakeIdentity:
    username = "aliachin11"
    user_id = "7055967621082039297"
    sec_uid = "MS4wLjABAAAAbenchmarkSecUidValue"


class FakeItem:
    def __init__(self, post_id, *, photo=False, image_urls=()):
        self.post_id = post_id
        self.is_photo = photo
        self.description = "scanner caption"
        self.image_urls = image_urls


class FakeScannerClient:
    def __init__(self):
        self.calls = []

    def creator_data(self, username):
        self.calls.append(("creator", username))
        return {"userInfo": {"code": 0}, "videoList": []}

    def identity_from_creator(self, creator):
        return FakeIdentity()

    def collect_posts(self, sec_uid, *, profile_url, recent, is_private):
        self.calls.append(("collect", sec_uid, profile_url, recent, is_private))
        return [
            FakeItem("7176363825556376859"),
            FakeItem(
                "7176363825556376860", photo=True,
                image_urls=(
                    ("https://images.example.invalid/0.jpg", "https://fallback.example.invalid/0.jpg"),
                    ("https://images.example.invalid/1.jpg",),
                    ("http://unsafe.example.invalid/2.jpg", "https://127.0.0.1/private.jpg"),
                ),
            ),
        ]


def test_tt_dlp_scanner_normalizes_identity_and_canonical_post_urls_without_cookies():
    client = FakeScannerClient()
    scan = TikTokProfileScanner(lambda: client).scan("aliachin11")

    assert scan.profile.user_id == "7055967621082039297"
    assert scan.profile.sec_uid == "MS4wLjABAAAAbenchmarkSecUidValue"
    assert [post.canonical_url for post in scan.posts] == [
        "https://www.tiktok.com/@aliachin11/video/7176363825556376859",
        "https://www.tiktok.com/@aliachin11/photo/7176363825556376860",
    ]
    assert client.calls[0] == ("creator", "aliachin11")
    assert client.calls[1][-1] is False
    assert scan.posts[1].image_url_candidates == (
        ("https://images.example.invalid/0.jpg", "https://fallback.example.invalid/0.jpg"),
        ("https://images.example.invalid/1.jpg",),
        (),
    )


def test_tt_dlp_profile_preview_does_not_enumerate_posts():
    client = FakeScannerClient()

    profile = TikTokProfileScanner(lambda: client).resolve_profile("aliachin11")

    assert profile.username == "aliachin11"
    assert client.calls == [("creator", "aliachin11")]


def test_default_scanner_configuration_never_loads_cookies_or_authentication():
    source = inspect.getsource(TikTokProfileScanner._default_client)

    assert "cookies=None" in source
    assert "profile_store=None" in source
    assert "authenticated" not in source


class FakeScanner:
    def __init__(self, post_count=2, photo_indices=(), photo_candidate_groups_by_index=None):
        self.post_count = post_count
        self.photo_indices = set(photo_indices)
        self.photo_candidate_groups_by_index = photo_candidate_groups_by_index or {}
        self.calls = []
        self.thread_ids = []

    def scan(self, username):
        self.calls.append(username)
        self.thread_ids.append(threading.get_ident())
        return TikTokProfileScan(
            profile=TikTokScannedProfile(
                username="aliachin11", user_id="7055967621082039297",
                sec_uid="MS4wLjABAAAAbenchmarkSecUidValue",
            ),
            posts=[TikTokScannedPost(
                post_id=str(7176363825556376859 + index),
                canonical_url=(
                    f"https://www.tiktok.com/@aliachin11/"
                    f"{'photo' if index in self.photo_indices else 'video'}/{7176363825556376859 + index}"
                ),
                is_photo=index in self.photo_indices,
                description="scanner caption",
                image_url_candidates=self.photo_candidate_groups_by_index.get(index, ()),
            ) for index in range(self.post_count)],
        )


class FakeResolver:
    def __init__(self, failing_ids=()):
        self.urls = []
        self.failing_ids = set(failing_ids)

    async def resolve(self, url):
        self.urls.append(url)
        post_id = url.rsplit("/", 1)[-1]
        if post_id in self.failing_ids:
            raise SourceUnavailable("metadata unavailable")
        return {**BENCHMARK_ENTRY, "id": post_id, "webpage_url": url}


@pytest.mark.asyncio
async def test_per_post_yt_dlp_failure_keeps_scanned_post_and_other_metadata():
    scanner = FakeScanner(post_count=2)
    failed = str(7176363825556376860)
    resolver = FakeResolver(failing_ids={failed})

    profile, posts = await TikTokProfileProvider(scanner, resolver).discover_profile("@aliachin11", limit=2)

    assert profile.platform_account_id == "7055967621082039297"
    assert [post.platform_post_id for post in posts] == ["7176363825556376859", failed]
    assert posts[0].engagement.view_count == 281
    assert posts[1].metadata["metadata_resolution_error"] == "SourceUnavailable"
    assert posts[1].original_url.endswith(failed)
    assert resolver.urls == [post.original_url for post in posts]


@pytest.mark.asyncio
async def test_scanner_confirmed_photo_keeps_type_and_never_gets_video_asset():
    scanner = FakeScanner(
        post_count=2,
        photo_indices={1},
        photo_candidate_groups_by_index={
            1: (("https://images.example.invalid/0.jpg",), ("https://images.example.invalid/1.jpg",)),
        },
    )
    resolver = FakeResolver()

    _, posts = await TikTokProfileProvider(scanner, resolver).discover_profile("@aliachin11", limit=2)

    assert posts[0].media_type == "video"
    assert posts[0].assets[0].asset_type == "video"
    assert posts[1].media_type == "carousel"
    assert [(asset.position, asset.asset_type) for asset in posts[1].assets] == [(0, "photo"), (1, "photo")]
    assert all(asset.asset_type != "video" for asset in posts[1].assets)
    assert posts[1].original_url == resolver.urls[1]


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
