from datetime import datetime, timezone

import pytest

from providers.base import MediaNotFound, SourceUnavailable
from providers.tiktok.apify_highlight_story_media_provider import (
    ApifyHighlightStoryMediaProvider,
    PROVIDER_NAMESPACE,
)
from providers.tiktok.apify_highlights import HighlightStory, HighlightStoryPage


class Client:
    def __init__(self, stories):
        self._stories = stories
        self.calls = []

    async def stories(self, collection_id, *, posts_per_page, expected_item_count, live_authorized):
        self.calls.append((collection_id, posts_per_page, expected_item_count, live_authorized))
        return HighlightStoryPage(tuple(self._stories), len(self._stories), 0, posts_per_page, False)


@pytest.mark.asyncio
async def test_apify_story_adapter_returns_one_ordered_video_descriptor_in_memory():
    client = Client((HighlightStory(
        "story", "collection", 7, "video", datetime(2026, 1, 1, tzinfo=timezone.utc), 3.5,
        ("https://ephemeral.invalid/primary", "https://ephemeral.invalid/fallback"),
    ),))
    descriptors = await ApifyHighlightStoryMediaProvider(client).resolve_story_media(
        username="creator", provider_namespace=PROVIDER_NAMESPACE,
        provider_collection_id="collection", provider_item_id="story",
    )
    assert [(item.position, item.asset_type, item.duration_seconds) for item in descriptors] == [(0, "video", 3.5)]
    assert descriptors[0].candidates == ("https://ephemeral.invalid/primary", "https://ephemeral.invalid/fallback")
    assert client.calls == [("collection", 30, None, False)]


@pytest.mark.asyncio
async def test_apify_story_adapter_preserves_photo_descriptor_order_without_registration():
    client = Client((HighlightStory(
        "story", "collection", 0, "photo", None, None,
        ("https://ephemeral.invalid/0", "https://ephemeral.invalid/1", "https://ephemeral.invalid/2"),
    ),))
    descriptors = await ApifyHighlightStoryMediaProvider(client).resolve_story_media(
        username="creator", provider_namespace=PROVIDER_NAMESPACE,
        provider_collection_id="collection", provider_item_id="story",
    )
    assert [(item.position, item.asset_type, item.candidates) for item in descriptors] == [
        (0, "photo", ("https://ephemeral.invalid/0",)),
        (1, "photo", ("https://ephemeral.invalid/1",)),
        (2, "photo", ("https://ephemeral.invalid/2",)),
    ]
    assert client.calls[0][-1] is False


@pytest.mark.asyncio
async def test_apify_story_adapter_rejects_wrong_namespace_or_unavailable_story_without_descriptor_leakage():
    client = Client(())
    provider = ApifyHighlightStoryMediaProvider(client)
    with pytest.raises(MediaNotFound):
        await provider.resolve_story_media(
            username="creator", provider_namespace="other", provider_collection_id="collection", provider_item_id="story",
        )
    with pytest.raises(MediaNotFound) as error:
        await provider.resolve_story_media(
            username="creator", provider_namespace=PROVIDER_NAMESPACE,
            provider_collection_id="collection", provider_item_id="missing",
        )
    assert "ephemeral.invalid" not in str(error.value)


@pytest.mark.asyncio
async def test_apify_story_adapter_rejects_invalid_collection_response_safely():
    class BadClient:
        async def stories(self, *_args, **_kwargs):
            return object()
    with pytest.raises(SourceUnavailable) as error:
        await ApifyHighlightStoryMediaProvider(BadClient()).resolve_story_media(
            username="creator", provider_namespace=PROVIDER_NAMESPACE,
            provider_collection_id="collection", provider_item_id="story",
        )
    assert "collection" not in str(error.value)


@pytest.mark.asyncio
async def test_apify_story_adapter_defaults_to_no_live_authorization():
    client = Client((HighlightStory("story", "collection", 0, "video", None, None, ("https://ephemeral.invalid/v",)),))
    await ApifyHighlightStoryMediaProvider(client).resolve_story_media(
        username="creator", provider_namespace=PROVIDER_NAMESPACE,
        provider_collection_id="collection", provider_item_id="story",
    )
    assert client.calls[-1][-1] is False
