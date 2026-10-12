from datetime import datetime, timezone

import pytest

from providers.tiktok.apify_highlights import (
    HighlightCollection,
    HighlightIncompleteResponse,
    HighlightStory,
    HighlightStoryPage,
    TikTokHighlightsAuthenticationError,
)
from providers.tiktok.apify_highlights_provider import (
    ApifyHighlightsDiscoveryProvider,
    PROVIDER_NAMESPACE,
)


class Client:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.calls = []

    async def discover(self, username, *, live_authorized):
        self.calls.append(("discover", username, live_authorized))
        return (
            HighlightCollection("heart", "Heart", 2, "descriptor-not-persisted"),
            HighlightCollection("empty", "Empty", 0),
        )

    async def stories(self, collection_id, *, posts_per_page, expected_item_count, live_authorized):
        self.calls.append(("stories", collection_id, posts_per_page, expected_item_count, live_authorized))
        if self.failure == collection_id:
            raise TikTokHighlightsAuthenticationError("private provider detail")
        stories = () if collection_id == "empty" else (
            HighlightStory("item-1", collection_id, 0, "video", datetime(2026, 1, 1, tzinfo=timezone.utc), 3.0,
                           ("https://provider.invalid/private",), "https://provider.invalid/cover"),
            HighlightStory("item-2", collection_id, 1, "photo", None, None, (), None),
        )
        return HighlightStoryPage(stories, len(stories), 0, posts_per_page, False)


@pytest.mark.asyncio
async def test_adapter_preserves_provider_positions_without_persisting_descriptors():
    client = Client()
    result = await ApifyHighlightsDiscoveryProvider(client).discover_highlights("creator")
    assert result.incomplete_collections == result.failed_collections == 0
    assert [value.provider_collection_id for value in result.snapshots] == ["heart", "empty"]
    assert [item.provider_position for item in result.snapshots[0].memberships] == [0, 1]
    assert [item.media_type for item in result.snapshots[0].memberships] == ["video", "photo"]
    assert all(value.provider_namespace == PROVIDER_NAMESPACE for value in result.snapshots)
    assert not result.snapshots[0].upstream_completeness_verified
    assert all(call[-1] is False for call in client.calls)
    assert "provider.invalid" not in repr(result)


@pytest.mark.asyncio
async def test_one_collection_failure_preserves_other_snapshots_as_partial_failure_data():
    result = await ApifyHighlightsDiscoveryProvider(Client(failure="heart")).discover_highlights("creator")
    assert result.failed_collections == 1
    assert [value.provider_collection_id for value in result.snapshots] == ["empty"]


@pytest.mark.asyncio
async def test_unknown_or_oversized_count_is_incomplete_without_calling_story_actor():
    class CountClient(Client):
        async def discover(self, username, *, live_authorized):
            return (HighlightCollection("unknown", "Unknown", None), HighlightCollection("large", "Large", 101))

    client = CountClient()
    result = await ApifyHighlightsDiscoveryProvider(client).discover_highlights("creator")
    assert result.snapshots == ()
    assert result.incomplete_collections == 2
    assert not [call for call in client.calls if call[0] == "stories"]
