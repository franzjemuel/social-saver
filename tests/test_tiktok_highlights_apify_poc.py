import json

import httpx
import pytest

from providers.tiktok.apify_highlights import (
    ApifyTikTokHighlightsPoc,
    HighlightCollectionUnavailable,
    HighlightIncompleteResponse,
    HighlightProfileNotFound,
    LiveHighlightsCallNotAuthorized,
    TikTokHighlightsPocError,
    _command,
    normalize_collections,
    normalize_stories,
)


COLLECTIONS = [
    {"source": "tiktok", "unique_id": "example_user", "highlight_id": "collection-a", "title": "First", "post_count": 3, "cover": "https://media.example.invalid/cover-a.jpg"},
    {"source": "tiktok", "unique_id": "example_user", "highlight_id": "collection-b", "title": "Second", "post_count": 0},
]
STORIES = [
    {"highlight_id": "collection-a", "type": "video", "video_id": "story-1", "create_time": 1724980000, "duration": 4.2, "video_url": "https://media.example.invalid/video-1.mp4", "cover_url": "https://media.example.invalid/cover-1.jpg"},
    {"highlight_id": "collection-a", "type": "photo", "aweme_id": "story-2", "create_time_date": "2024-08-30T01:00:00Z", "images": ["https://media.example.invalid/photo-1.jpg", "https://media.example.invalid/photo-2.jpg"]},
    {"highlight_id": "collection-a", "type": "video", "video_id": "story-3", "video_url": "https://media.example.invalid/video-3.mp4"},
]


def test_collection_normalization_preserves_provider_order_and_safe_summary_hides_urls():
    collections = normalize_collections(COLLECTIONS, username="example_user")
    assert [item.provider_collection_id for item in collections] == ["collection-a", "collection-b"]
    assert collections[0].title == "First"
    assert collections[0].reported_item_count == 3
    assert "example.invalid" not in json.dumps(collections[0].safe_summary())


def test_story_normalization_preserves_sequence_video_and_photo_metadata_without_leaking_urls():
    page = normalize_stories(STORIES, collection_id="collection-a", requested_posts_per_page=2)
    assert [item.provider_item_id for item in page.stories] == ["story-1", "story-2", "story-3"]
    assert [item.position for item in page.stories] == [0, 1, 2]
    assert [item.media_type for item in page.stories] == ["video", "photo", "video"]
    assert page.stories[0].duration_seconds == 4.2
    assert page.stories[1].published_at is not None
    assert page.provider_pagination_observed is True
    assert "example.invalid" not in json.dumps(page.safe_summary())
    assert "example.invalid" not in json.dumps(page.stories[0].safe_summary())


def test_duplicate_story_records_are_deduplicated_without_reordering_remaining_items():
    page = normalize_stories(STORIES + [STORIES[1]], collection_id="collection-a", requested_posts_per_page=10)
    assert [item.provider_item_id for item in page.stories] == ["story-1", "story-2", "story-3"]
    assert page.raw_item_count == 4
    assert page.duplicate_item_count == 1


@pytest.mark.parametrize("rows", [[], [{"highlight_id": "collection-a", "type": "video"}], [{"highlight_id": "wrong", "type": "video", "video_id": "x"}]])
def test_incomplete_story_rows_are_rejected(rows):
    if rows == []:
        assert normalize_stories(rows, collection_id="collection-a", requested_posts_per_page=10).stories == ()
    else:
        with pytest.raises(HighlightIncompleteResponse):
            normalize_stories(rows, collection_id="collection-a", requested_posts_per_page=10)


def test_incomplete_collection_rows_are_rejected():
    with pytest.raises(HighlightIncompleteResponse):
        normalize_collections([{"unique_id": "example_user"}], username="example_user")
    with pytest.raises(HighlightIncompleteResponse):
        normalize_collections([{**COLLECTIONS[0], "unique_id": "other"}], username="example_user")


def test_empty_collection_discovery_is_a_valid_no_highlights_result():
    assert normalize_collections([], username="example_user") == ()


@pytest.mark.asyncio
async def test_live_call_is_gated_before_network(monkeypatch):
    monkeypatch.delenv("SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE", raising=False)
    with pytest.raises(LiveHighlightsCallNotAuthorized):
        await ApifyTikTokHighlightsPoc("token", max_charge_usd=0.10).discover("example_user", live_authorized=True)


@pytest.mark.asyncio
async def test_documented_apify_payload_and_normalized_discovery(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE", "1")
    def handler(request):
        assert request.url.path.endswith("igview-owner~tiktok-highlight-scraper/run-sync-get-dataset-items")
        assert request.headers["authorization"] == "Bearer private-token"
        assert request.url.params["maxTotalChargeUsd"] == "0.10"
        assert json.loads(request.content) == {"mode": "highlight_list", "uniqueIds": ["example_user"]}
        return httpx.Response(200, json=COLLECTIONS)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    collections = await ApifyTikTokHighlightsPoc("private-token", max_charge_usd=0.10).discover("@example_user", live_authorized=True)
    assert [item.provider_collection_id for item in collections] == ["collection-a", "collection-b"]


@pytest.mark.asyncio
async def test_documented_apify_story_payload_and_http_errors(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE", "1")
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert json.loads(request.content) == {"mode": "highlight_posts", "highlightIds": ["collection-a"], "postsPerPage": 2}
            return httpx.Response(200, json=STORIES)
        return httpx.Response(404, json=[])
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    client = ApifyTikTokHighlightsPoc("private-token", max_charge_usd=0.10)
    assert (await client.stories("collection-a", posts_per_page=2, live_authorized=True)).provider_pagination_observed
    with pytest.raises(HighlightCollectionUnavailable):
        await client.stories("collection-a", posts_per_page=2, live_authorized=True)


@pytest.mark.asyncio
async def test_profile_not_found_and_provider_failure_are_sanitized(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE", "1")
    statuses = iter((404, 502))
    def handler(request):
        return httpx.Response(next(statuses), json={"untrusted": "provider detail"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    client = ApifyTikTokHighlightsPoc("private-token", max_charge_usd=0.10)
    with pytest.raises(HighlightProfileNotFound):
        await client.discover("example_user", live_authorized=True)
    with pytest.raises(TikTokHighlightsPocError, match="HTTP_502"):
        await client.discover("example_user", live_authorized=True)


def test_dry_run_input_validation_and_bounds():
    assert ApifyTikTokHighlightsPoc.discovery_input("https://www.tiktok.com/@example_user") == {"mode": "highlight_list", "uniqueIds": ["example_user"]}
    assert ApifyTikTokHighlightsPoc.stories_input("collection-a", posts_per_page=30)["postsPerPage"] == 30
    with pytest.raises(ValueError):
        ApifyTikTokHighlightsPoc.stories_input("collection-a", posts_per_page=31)
    with pytest.raises(Exception):
        ApifyTikTokHighlightsPoc.discovery_input("https://www.tiktok.com/@example_user/video/123")


@pytest.mark.asyncio
async def test_manual_command_dry_run_needs_no_token_or_network(monkeypatch):
    monkeypatch.delenv("APIFY_TIKTOK_HIGHLIGHTS_TOKEN", raising=False)
    result = await _command(type("Args", (), {
        "mode": "highlight_list", "username": "@example_user", "collection_id": None,
        "posts_per_page": 10, "max_charge_usd": 0.10, "live": False,
    })())
    assert result == {
        "dry_run": True,
        "actor": "igview-owner~tiktok-highlight-scraper",
        "payload": {"mode": "highlight_list", "uniqueIds": ["example_user"]},
        "max_charge_usd": 0.10,
    }
