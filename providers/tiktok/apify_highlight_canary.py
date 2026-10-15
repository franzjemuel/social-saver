"""Disabled-by-default asynchronous Apify bridge for one Highlight canary.

This module has no environment-variable reads and registers no provider factory.
The worker must inject both a token-bearing client and a durable execution
broker. Descriptor URLs exist only in the returned in-memory Story object.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from providers.base import HighlightStoryMediaDescriptor, MediaNotFound
from providers.tiktok.apify_highlights import (
    ACTOR_ID,
    MAX_DATASET_ITEMS,
    HighlightCollectionUnavailable,
    HighlightIncompleteResponse,
    HighlightStory,
    TikTokHighlightsPocError,
    ApifyTikTokHighlightsPoc,
    normalize_collections,
    normalize_stories,
)
from providers.tiktok.apify_highlights_provider import PROVIDER_NAMESPACE


_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ApifyCanaryKnownFailure(TikTokHighlightsPocError):
    """The Actor definitively rejected or failed a known run."""


class ApifyCanaryIndeterminate(TikTokHighlightsPocError):
    """A request may have reached Apify; it must never be retried automatically."""


@dataclass(frozen=True)
class ApifyRunResult:
    run_id: str
    rows: list[dict[str, Any]]


class ApifyCanaryExecutor(Protocol):
    async def invoke(
        self,
        operation: str,
        payload: dict[str, object],
        client: "ApifyAsyncHighlightActorClient",
    ) -> list[dict[str, Any]]: ...


class ApifyAsyncHighlightActorClient:
    """Small documented Actor-runs client with no automatic paid retries."""

    def __init__(
        self,
        token: str,
        *,
        actor_timeout_seconds: int = 180,
        poll_seconds: float = 5.0,
    ):
        if not isinstance(token, str) or not token:
            raise ValueError("missing Apify Highlight token")
        if not 1 <= actor_timeout_seconds <= 300:
            raise ValueError("invalid Apify Highlight timeout")
        if not 0.1 <= poll_seconds <= 30:
            raise ValueError("invalid Apify Highlight polling interval")
        self._token = token
        self._actor_timeout_seconds = actor_timeout_seconds
        self._poll_seconds = poll_seconds

    @staticmethod
    def _charge(maximum_micro_usd: int) -> str:
        if not isinstance(maximum_micro_usd, int) or not 1 <= maximum_micro_usd <= 650_000:
            raise ValueError("invalid Apify Highlight canary charge limit")
        return format(Decimal(maximum_micro_usd) / Decimal(1_000_000), ".6f")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def start_run(self, payload: dict[str, object], *, maximum_micro_usd: int) -> str:
        """Start exactly one asynchronous run and return only its opaque receipt."""
        endpoint = f"https://api.apify.com/v2/acts/{quote(ACTOR_ID, safe='~')}/runs"
        params = {
            "waitForFinish": "0",
            "timeout": str(self._actor_timeout_seconds),
            "maxTotalChargeUsd": self._charge(maximum_micro_usd),
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._actor_timeout_seconds + 15),
                follow_redirects=False,
            ) as client:
                response = await client.post(endpoint, params=params, headers=self._headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dispatch was indeterminate") from exc
        except httpx.HTTPError as exc:
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dispatch was indeterminate") from exc
        if response.status_code in {401, 403, 402, 404, 422}:
            raise ApifyCanaryKnownFailure("TikTok Highlight provider rejected the canary run")
        if response.status_code >= 400:
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dispatch was indeterminate")
        try:
            body = response.json()
            run_id = body["data"]["id"]
        except (TypeError, KeyError, ValueError) as exc:
            # A successful HTTP response may still have created a run. Never
            # issue another request when its receipt cannot be established.
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dispatch was indeterminate") from exc
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dispatch was indeterminate")
        return run_id

    async def collect_run(self, run_id: str) -> list[dict[str, Any]]:
        """Poll one already-receipted run; this never creates another run."""
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ApifyCanaryKnownFailure("TikTok Highlight provider receipt was invalid")
        deadline = time.monotonic() + self._actor_timeout_seconds
        run_endpoint = f"https://api.apify.com/v2/actor-runs/{quote(run_id, safe='~')}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(min(30, self._actor_timeout_seconds)),
            follow_redirects=False,
        ) as client:
            while True:
                try:
                    response = await client.get(run_endpoint, headers=self._headers)
                except httpx.TimeoutException as exc:
                    raise ApifyCanaryIndeterminate("TikTok Highlight provider polling was indeterminate") from exc
                except httpx.HTTPError as exc:
                    raise ApifyCanaryIndeterminate("TikTok Highlight provider polling was indeterminate") from exc
                if response.status_code in {401, 403, 404}:
                    raise ApifyCanaryKnownFailure("TikTok Highlight provider run was unavailable")
                if response.status_code >= 400:
                    raise ApifyCanaryIndeterminate("TikTok Highlight provider polling was indeterminate")
                try:
                    run = response.json()["data"]
                    status = run["status"]
                except (TypeError, KeyError, ValueError) as exc:
                    raise ApifyCanaryIndeterminate("TikTok Highlight provider polling was indeterminate") from exc
                if status == "SUCCEEDED":
                    dataset_id = run.get("defaultDatasetId")
                    if not isinstance(dataset_id, str) or not _RUN_ID.fullmatch(dataset_id):
                        raise ApifyCanaryKnownFailure("TikTok Highlight provider dataset was unavailable")
                    return await self._dataset_items(client, dataset_id)
                if status in {"FAILED", "ABORTED", "TIMED-OUT"}:
                    raise ApifyCanaryKnownFailure("TikTok Highlight provider run failed")
                if not isinstance(status, str) or time.monotonic() >= deadline:
                    raise ApifyCanaryIndeterminate("TikTok Highlight provider polling was indeterminate")
                await asyncio.sleep(self._poll_seconds)

    async def _dataset_items(self, client: httpx.AsyncClient, dataset_id: str) -> list[dict[str, Any]]:
        endpoint = f"https://api.apify.com/v2/datasets/{quote(dataset_id, safe='~')}/items"
        try:
            response = await client.get(
                endpoint,
                params={"clean": "true", "format": "json", "limit": str(MAX_DATASET_ITEMS)},
                headers=self._headers,
            )
        except httpx.TimeoutException as exc:
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dataset retrieval was indeterminate") from exc
        except httpx.HTTPError as exc:
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dataset retrieval was indeterminate") from exc
        if response.status_code in {401, 403, 404}:
            raise ApifyCanaryKnownFailure("TikTok Highlight provider dataset was unavailable")
        if response.status_code >= 400:
            raise ApifyCanaryIndeterminate("TikTok Highlight provider dataset retrieval was indeterminate")
        try:
            rows = response.json()
        except ValueError as exc:
            raise ApifyCanaryKnownFailure("TikTok Highlight provider returned invalid data") from exc
        if not isinstance(rows, list) or len(rows) > MAX_DATASET_ITEMS or not all(isinstance(row, dict) for row in rows):
            raise ApifyCanaryKnownFailure("TikTok Highlight provider returned invalid data")
        return rows


class ApifyHighlightCanaryMediaProvider:
    """Resolve one already-owned video Story through two bounded Actor runs."""

    requires_execution_policy = True
    requires_live_execution_broker = True

    def __init__(self, client: ApifyAsyncHighlightActorClient, *, posts_per_page: int = 10):
        if not 1 <= posts_per_page <= 30:
            raise ValueError("invalid Highlight Story page size")
        self._client = client
        self._posts_per_page = posts_per_page

    async def resolve_story_media(
        self,
        *,
        username: str,
        provider_namespace: str,
        provider_collection_id: str,
        provider_item_id: str,
        execution: ApifyCanaryExecutor,
    ) -> tuple[HighlightStoryMediaDescriptor, ...]:
        if provider_namespace != PROVIDER_NAMESPACE:
            raise MediaNotFound("TikTok Highlight media provider is unavailable")
        discovery_rows = await execution.invoke(
            "collection_list", ApifyTikTokHighlightsPoc.discovery_input(username), self._client,
        )
        collections = normalize_collections(discovery_rows, username=username)
        collection = next(
            (item for item in collections if item.provider_collection_id == provider_collection_id), None,
        )
        if collection is None:
            raise HighlightCollectionUnavailable("TikTok Highlight collection was unavailable")
        story_rows = await execution.invoke(
            "collection_items",
            ApifyTikTokHighlightsPoc.stories_input(
                provider_collection_id, posts_per_page=self._posts_per_page,
            ),
            self._client,
        )
        page = normalize_stories(
            story_rows,
            collection_id=provider_collection_id,
            requested_posts_per_page=self._posts_per_page,
            expected_item_count=collection.reported_item_count,
        )
        story = next((item for item in page.stories if item.provider_item_id == provider_item_id), None)
        if story is None:
            raise MediaNotFound("TikTok Highlight Story was unavailable")
        return self._video_descriptor(story, provider_collection_id)

    @staticmethod
    def _video_descriptor(
        story: HighlightStory, collection_id: str,
    ) -> tuple[HighlightStoryMediaDescriptor, ...]:
        if story.provider_collection_id != collection_id:
            raise HighlightIncompleteResponse("TikTok Highlight provider returned an invalid response")
        if story.media_type != "video":
            raise MediaNotFound("TikTok Highlight canary requires a video Story")
        if not story.media_descriptors:
            raise MediaNotFound("TikTok Highlight video descriptor was unavailable")
        return (
            HighlightStoryMediaDescriptor(
                position=0,
                asset_type="video",
                candidates=tuple(story.media_descriptors),
                duration_seconds=story.duration_seconds,
            ),
        )
