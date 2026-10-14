"""Test-injected bridge from the isolated Apify POC to Story media descriptors.

This module deliberately registers no factory and never reads an Apify token.
The worker receives it only through an explicit dependency injection seam in
tests or a later, separately approved live-provider phase.  Its transient
candidate URLs are returned directly to the worker and must never cross a
database, queue, log, or browser boundary.
"""
from __future__ import annotations

from typing import Protocol

from providers.base import (
    HighlightStoryMediaDescriptor,
    HighlightStoryMediaProvider,
    MediaNotFound,
    SourceUnavailable,
)
from providers.tiktok.apify_highlights import (
    HighlightIncompleteResponse,
    HighlightStory,
    HighlightStoryPage,
    TikTokHighlightsPocError,
)
from providers.tiktok.apify_highlights_provider import PROVIDER_NAMESPACE


class ApifyHighlightStoryClient(Protocol):
    """Narrow, mockable subset of the isolated POC contract."""

    async def stories(
        self,
        collection_id: str,
        *,
        posts_per_page: int,
        expected_item_count: int | None,
        live_authorized: bool,
    ) -> HighlightStoryPage: ...


class ApifyHighlightStoryMediaProvider(HighlightStoryMediaProvider):
    """Find one Story and translate transient Actor descriptors in memory.

    Live authorization defaults to false.  The only code path that could set
    it true must also inject the worker's durable invocation policy; no API or
    hosted worker factory constructs this adapter in this increment.
    """

    requires_execution_policy = True

    def __init__(
        self, client: ApifyHighlightStoryClient, *, max_stories: int = 30,
        live_authorized: bool = False,
    ):
        if not 1 <= max_stories <= 30:
            raise ValueError("invalid Apify Highlight Story lookup limit")
        self._client = client
        self._max_stories = max_stories
        self._live_authorized = live_authorized

    async def resolve_story_media(
        self,
        *,
        username: str,
        provider_namespace: str,
        provider_collection_id: str,
        provider_item_id: str,
    ) -> tuple[HighlightStoryMediaDescriptor, ...]:
        # ``username`` is intentionally not sent to the current Actor's
        # collection-items contract.  It was authenticated to the owned
        # archive target by the repository before this point.
        del username
        if provider_namespace != PROVIDER_NAMESPACE:
            raise MediaNotFound("TikTok Highlight media provider is unavailable")
        try:
            page = await self._client.stories(
                provider_collection_id,
                posts_per_page=self._max_stories,
                expected_item_count=None,
                live_authorized=self._live_authorized,
            )
        except TikTokHighlightsPocError:
            # The POC classes already carry sanitized text; preserve only a
            # generic source category across this provider boundary.
            raise SourceUnavailable("TikTok Highlight media provider failed") from None
        if not isinstance(page, HighlightStoryPage):
            raise SourceUnavailable("TikTok Highlight media provider returned an invalid response")
        story = next((item for item in page.stories if item.provider_item_id == provider_item_id), None)
        if story is None:
            raise MediaNotFound("TikTok Highlight Story was unavailable")
        return self._descriptors(story, provider_collection_id)

    @staticmethod
    def _descriptors(
        story: HighlightStory, provider_collection_id: str,
    ) -> tuple[HighlightStoryMediaDescriptor, ...]:
        if story.provider_collection_id != provider_collection_id:
            raise SourceUnavailable("TikTok Highlight media provider returned an invalid response")
        if story.media_type == "video":
            if not story.media_descriptors:
                raise MediaNotFound("TikTok Highlight video descriptor was unavailable")
            return (HighlightStoryMediaDescriptor(
                position=0,
                asset_type="video",
                candidates=tuple(story.media_descriptors),
                duration_seconds=story.duration_seconds,
            ),)
        if story.media_type == "photo":
            if not story.media_descriptors:
                raise MediaNotFound("TikTok Highlight photo descriptor was unavailable")
            # The POC contract supplies ordered image descriptors, not a
            # candidate group per image. Preserve that observed order.
            return tuple(
                HighlightStoryMediaDescriptor(position=index, asset_type="photo", candidates=(candidate,))
                for index, candidate in enumerate(story.media_descriptors)
            )
        raise HighlightIncompleteResponse("TikTok Highlight media type was unsupported")
