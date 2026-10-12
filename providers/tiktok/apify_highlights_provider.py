"""Provider-neutral, mockable adapter around the isolated Apify Highlights POC.

This is deliberately not registered with a worker.  A future live phase needs
durable per-Actor invocation records and approved licensing/spend controls
before any factory may construct it from environment configuration.
"""
from __future__ import annotations

from providers.base import (
    ArchivedHighlightCollectionSnapshot,
    ArchivedHighlightStoryMembership,
    HighlightDiscoveryResult,
    ProfileHighlightsDiscoveryProvider,
)
from providers.tiktok.apify_highlights import (
    ApifyTikTokHighlightsPoc,
    HighlightIncompleteResponse,
    TikTokHighlightsPocError,
)


PROVIDER_NAMESPACE = "apify:igview-owner/tiktok-highlight-scraper"


class ApifyHighlightsDiscoveryProvider(ProfileHighlightsDiscoveryProvider):
    """Normalize collection and Story metadata without retaining descriptors."""

    def __init__(
        self,
        client: ApifyTikTokHighlightsPoc,
        *,
        max_collections: int = 10,
        max_stories_per_collection: int = 99,
    ):
        # The POC caps synchronous datasets at 100 and accepts expected counts
        # below that cap, so 99 is the largest safe complete-enumeration bound.
        if not 1 <= max_collections <= 100 or not 1 <= max_stories_per_collection <= 99:
            raise ValueError("invalid Apify Highlight discovery limits")
        self._client = client
        self._max_collections = max_collections
        self._max_stories_per_collection = max_stories_per_collection

    async def discover_highlights(self, username: str) -> HighlightDiscoveryResult:
        collections = await self._client.discover(username, live_authorized=False)
        if len(collections) > self._max_collections:
            raise HighlightIncompleteResponse("TikTok Highlights provider exceeded collection limit")

        snapshots = []
        incomplete = failed = 0
        for collection in collections:
            # Count mismatches are incomplete observations, not reasons to
            # discard independent collections from this same provider run.
            if (
                collection.reported_item_count is None
                or collection.reported_item_count > self._max_stories_per_collection
            ):
                incomplete += 1
                continue
            try:
                page = await self._client.stories(
                    collection.provider_collection_id,
                    posts_per_page=min(30, self._max_stories_per_collection),
                    expected_item_count=collection.reported_item_count,
                    live_authorized=False,
                )
            except HighlightIncompleteResponse:
                incomplete += 1
                continue
            except TikTokHighlightsPocError:
                failed += 1
                continue
            snapshots.append(ArchivedHighlightCollectionSnapshot(
                provider_namespace=PROVIDER_NAMESPACE,
                provider_collection_id=collection.provider_collection_id,
                title=collection.title,
                reported_item_count=collection.reported_item_count,
                has_cover_descriptor=collection.cover_descriptor is not None,
                observation_complete=(page.duplicate_item_count == 0),
                memberships=tuple(
                    ArchivedHighlightStoryMembership(
                        provider_namespace=PROVIDER_NAMESPACE,
                        provider_item_id=story.provider_item_id,
                        provider_position=story.position,
                        media_type=story.media_type,
                        published_at=story.published_at,
                        duration_seconds=story.duration_seconds,
                        has_media_descriptor=bool(story.media_descriptors),
                        has_cover_descriptor=story.cover_descriptor is not None,
                    )
                    for story in page.stories
                ),
                # Actor output alone is not independent upstream verification.
                upstream_completeness_verified=False,
            ))
        return HighlightDiscoveryResult(tuple(snapshots), incomplete, failed)
