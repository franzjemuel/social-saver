import json

import httpx
import pytest

from providers.base import SourceUnavailable, UnsupportedUrl
from providers.instagram.apify_stories import ApifyInstagramStoriesProvider, normalize_public_profile


def test_normalize_public_profile():
    assert normalize_public_profile("@nasa") == "nasa"
    assert normalize_public_profile("https://www.instagram.com/nasa/?x=1") == "nasa"
    with pytest.raises(UnsupportedUrl):
        normalize_public_profile("https://example.com/nasa")


@pytest.mark.asyncio
async def test_resolves_public_story_rows(monkeypatch):
    def handler(request):
        assert request.headers["authorization"] == "Bearer private-token"
        assert request.url.params["maxTotalChargeUsd"] == "0.05"
        assert json.loads(request.content) == {"targets": ["nasa"], "scrapeType": "stories"}
        return httpx.Response(200, json=[{
            "story_id": "123", "source_username": "nasa", "item_type": "story",
            "media_type": "video", "video_url": "https://cdn.example/story.mp4",
            "image_url": "https://cdn.example/story.jpg", "video_duration": 4.2,
            "taken_at": 1724980000,
        }])
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    result = await ApifyInstagramStoriesProvider("private-token", "actor", limit=3).resolve("nasa")
    assert len(result) == 1
    assert result[0].platform_media_id == "123"
    assert result[0].assets[0].asset_type == "video"
    assert result[0].strategy == "apify_public_stories_v1"


@pytest.mark.asyncio
async def test_missing_token_stops_before_network():
    with pytest.raises(SourceUnavailable, match="not configured"):
        await ApifyInstagramStoriesProvider(None, "actor").resolve("nasa")
