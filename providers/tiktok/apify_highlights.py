"""Isolated, opt-in Apify TikTok Highlights discovery proof of concept.

This module intentionally is not wired into Social Saver's API, worker, or
archive models.  It evaluates one documented third-party Actor contract using
an explicit paid-call gate.  Returned media descriptors stay in memory and the
provided command prints only a safe summary.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

import httpx

from providers.base import SourceUnavailable, UnsupportedUrl
from providers.tiktok.provider import normalize_tiktok_profile_target


ACTOR_ID = "igview-owner~tiktok-highlight-scraper"
TOKEN_ENV = "APIFY_TIKTOK_HIGHLIGHTS_TOKEN"
LIVE_GATE_ENV = "SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE"
MAX_POSTS_PER_PAGE = 30
DEFAULT_TIMEOUT_SECONDS = 120


class TikTokHighlightsPocError(SourceUnavailable):
    """A sanitized failure from the paid experimental provider."""


class HighlightProfileNotFound(TikTokHighlightsPocError):
    pass


class HighlightCollectionUnavailable(TikTokHighlightsPocError):
    pass


class HighlightIncompleteResponse(TikTokHighlightsPocError):
    pass


class LiveHighlightsCallNotAuthorized(TikTokHighlightsPocError):
    pass


@dataclass(frozen=True)
class HighlightCollection:
    """Provider-returned identifiers are deliberately not claimed TikTok-native."""

    provider_collection_id: str
    title: str | None
    reported_item_count: int | None
    cover_descriptor: str | None = None

    def safe_summary(self) -> dict[str, object]:
        return {
            "collection_id": self.provider_collection_id,
            "title": self.title,
            "reported_item_count": self.reported_item_count,
            "has_cover_descriptor": self.cover_descriptor is not None,
        }


@dataclass(frozen=True)
class HighlightStory:
    """One ordered Actor record; descriptors are worker-only future inputs."""

    provider_item_id: str
    provider_collection_id: str
    position: int
    media_type: Literal["video", "photo"]
    published_at: datetime | None
    duration_seconds: float | None
    media_descriptors: tuple[str, ...] = ()
    cover_descriptor: str | None = None

    def safe_summary(self) -> dict[str, object]:
        return {
            "item_id": self.provider_item_id,
            "collection_id": self.provider_collection_id,
            "position": self.position,
            "media_type": self.media_type,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "duration_seconds": self.duration_seconds,
            "has_media_descriptors": bool(self.media_descriptors),
            "has_cover_descriptor": self.cover_descriptor is not None,
        }


@dataclass(frozen=True)
class HighlightStoryPage:
    """Dataset output is flattened; inferred pagination is explicitly labelled."""

    stories: tuple[HighlightStory, ...]
    raw_item_count: int
    duplicate_item_count: int
    requested_posts_per_page: int
    provider_pagination_observed: bool

    def safe_summary(self) -> dict[str, object]:
        return {
            "story_count": len(self.stories),
            "raw_item_count": self.raw_item_count,
            "duplicate_item_count": self.duplicate_item_count,
            "requested_posts_per_page": self.requested_posts_per_page,
            "provider_pagination_observed": self.provider_pagination_observed,
        }


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _descriptor_urls(row: dict[str, Any], media_type: str) -> tuple[str, ...]:
    """Keep only HTTPS candidates, but never print or persist them in this POC."""
    raw: list[object]
    if media_type == "photo":
        raw = row.get("images") if isinstance(row.get("images"), list) else []
    else:
        raw = [row.get("video_url")]
    return tuple(value for value in raw if isinstance(value, str) and value.startswith("https://"))


def normalize_collections(rows: object, *, username: str) -> tuple[HighlightCollection, ...]:
    if not isinstance(rows, list):
        raise HighlightIncompleteResponse("TikTok Highlights provider returned an invalid response")
    collections: list[HighlightCollection] = []
    for row in rows:
        if not isinstance(row, dict):
            raise HighlightIncompleteResponse("TikTok Highlights provider returned an incomplete collection")
        owner = _string(row.get("unique_id"))
        collection_id = _string(row.get("highlight_id"))
        if owner is not None and owner.lower() != username.lower():
            raise HighlightIncompleteResponse("TikTok Highlights provider returned an unexpected profile")
        if collection_id is None:
            raise HighlightIncompleteResponse("TikTok Highlights provider returned a collection without an identifier")
        collections.append(HighlightCollection(
            provider_collection_id=collection_id,
            title=_string(row.get("title")),
            reported_item_count=_nonnegative_int(row.get("post_count")),
            cover_descriptor=_string(row.get("cover")),
        ))
    return tuple(collections)


def normalize_stories(
    rows: object,
    *,
    collection_id: str,
    requested_posts_per_page: int,
) -> HighlightStoryPage:
    if not isinstance(rows, list):
        raise HighlightIncompleteResponse("TikTok Highlights provider returned an invalid response")
    stories: list[HighlightStory] = []
    seen: set[str] = set()
    duplicates = 0
    for row in rows:
        if not isinstance(row, dict):
            raise HighlightIncompleteResponse("TikTok Highlights provider returned an incomplete story")
        parent = _string(row.get("highlight_id"))
        media_type = _string(row.get("type"))
        item_id = _string(row.get("video_id")) or _string(row.get("aweme_id"))
        if parent != collection_id:
            raise HighlightIncompleteResponse("TikTok Highlights provider returned a story for another collection")
        if media_type not in {"video", "photo"} or item_id is None:
            raise HighlightIncompleteResponse("TikTok Highlights provider returned a story without required metadata")
        if item_id in seen:
            duplicates += 1
            continue
        seen.add(item_id)
        duration = row.get("duration")
        duration_seconds = float(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0 else None
        stories.append(HighlightStory(
            provider_item_id=item_id,
            provider_collection_id=collection_id,
            position=len(stories),
            media_type=media_type,
            published_at=_optional_datetime(row.get("create_time_date")) or _optional_datetime(row.get("create_time")),
            duration_seconds=duration_seconds,
            media_descriptors=_descriptor_urls(row, media_type),
            cover_descriptor=_string(row.get("cover_url")),
        ))
    return HighlightStoryPage(
        stories=tuple(stories), raw_item_count=len(rows), duplicate_item_count=duplicates,
        requested_posts_per_page=requested_posts_per_page,
        provider_pagination_observed=len(rows) > requested_posts_per_page,
    )


class ApifyTikTokHighlightsPoc:
    """One-shot documented Actor client; never use this from a worker yet."""

    def __init__(self, token: str | None, *, max_charge_usd: float, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
        self._token = token
        self._max_charge_usd = max_charge_usd
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def discovery_input(username: str) -> dict[str, object]:
        normalized, _ = normalize_tiktok_profile_target(username)
        return {"mode": "highlight_list", "uniqueIds": [normalized]}

    @staticmethod
    def stories_input(collection_id: str, *, posts_per_page: int) -> dict[str, object]:
        normalized = _string(collection_id)
        if normalized is None or len(normalized) > 128:
            raise UnsupportedUrl("Invalid TikTok Highlight collection identifier")
        if not 1 <= posts_per_page <= MAX_POSTS_PER_PAGE:
            raise ValueError(f"posts_per_page must be 1-{MAX_POSTS_PER_PAGE}")
        return {"mode": "highlight_posts", "highlightIds": [normalized], "postsPerPage": posts_per_page}

    async def discover(self, username: str, *, live_authorized: bool = False) -> tuple[HighlightCollection, ...]:
        payload = self.discovery_input(username)
        rows = await self._run(payload, live_authorized=live_authorized, missing="profile")
        return normalize_collections(rows, username=payload["uniqueIds"][0])

    async def stories(
        self, collection_id: str, *, posts_per_page: int = 10, live_authorized: bool = False,
    ) -> HighlightStoryPage:
        payload = self.stories_input(collection_id, posts_per_page=posts_per_page)
        rows = await self._run(payload, live_authorized=live_authorized, missing="collection")
        return normalize_stories(rows, collection_id=payload["highlightIds"][0], requested_posts_per_page=posts_per_page)

    async def _run(self, payload: dict[str, object], *, live_authorized: bool, missing: Literal["profile", "collection"]) -> object:
        if not live_authorized or os.getenv(LIVE_GATE_ENV) != "1":
            raise LiveHighlightsCallNotAuthorized("Live TikTok Highlights calls require explicit authorization")
        if not self._token:
            raise LiveHighlightsCallNotAuthorized(f"{TOKEN_ENV} is not configured")
        if not 0 < self._max_charge_usd <= 1:
            raise ValueError("max_charge_usd must be greater than 0 and no more than 1")
        endpoint = f"https://api.apify.com/v2/acts/{quote(ACTOR_ID, safe='~')}/run-sync-get-dataset-items"
        params = {
            "clean": "true", "format": "json", "timeout": str(self._timeout_seconds),
            "maxTotalChargeUsd": f"{self._max_charge_usd:.2f}",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds + 30, follow_redirects=False) as client:
                response = await client.post(endpoint, params=params, headers={"Authorization": f"Bearer {self._token}"}, json=payload)
        except httpx.HTTPError as exc:
            raise TikTokHighlightsPocError(f"TikTok Highlights provider failed: {type(exc).__name__}") from exc
        if response.status_code == 404:
            if missing == "profile":
                raise HighlightProfileNotFound("TikTok Highlights profile was unavailable")
            raise HighlightCollectionUnavailable("TikTok Highlight collection was unavailable")
        if response.status_code >= 400:
            raise TikTokHighlightsPocError(f"TikTok Highlights provider failed: HTTP_{response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise HighlightIncompleteResponse("TikTok Highlights provider returned invalid JSON") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the isolated TikTok Highlights Apify discovery POC")
    parser.add_argument("--mode", required=True, choices=("highlight_list", "highlight_posts"))
    parser.add_argument("--username")
    parser.add_argument("--collection-id")
    parser.add_argument("--posts-per-page", type=int, default=10)
    parser.add_argument("--max-charge-usd", type=float, required=True)
    parser.add_argument("--live", action="store_true", help="Perform one explicitly authorized paid metadata-only request")
    return parser


async def _command(args: argparse.Namespace) -> dict[str, object]:
    if args.mode == "highlight_list":
        if not args.username:
            raise ValueError("--username is required for highlight_list")
        payload = ApifyTikTokHighlightsPoc.discovery_input(args.username)
    else:
        if not args.collection_id:
            raise ValueError("--collection-id is required for highlight_posts")
        payload = ApifyTikTokHighlightsPoc.stories_input(args.collection_id, posts_per_page=args.posts_per_page)

    if not args.live:
        return {
            "dry_run": True,
            "actor": ACTOR_ID,
            "payload": payload,
            "max_charge_usd": args.max_charge_usd,
        }

    client = ApifyTikTokHighlightsPoc(
        os.getenv(TOKEN_ENV), max_charge_usd=args.max_charge_usd,
    )
    if args.mode == "highlight_list":
        collections = await client.discover(args.username, live_authorized=True)
        return {
            "warning": "paid_metadata_only_run",
            "max_charge_usd": args.max_charge_usd,
            "collections": [collection.safe_summary() for collection in collections],
        }
    page = await client.stories(
        args.collection_id,
        posts_per_page=args.posts_per_page,
        live_authorized=True,
    )
    return {
        "warning": "paid_metadata_only_run",
        "max_charge_usd": args.max_charge_usd,
        "page": page.safe_summary(),
        "stories": [story.safe_summary() for story in page.stories],
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        print(asyncio.run(_command(args)))
    except (TikTokHighlightsPocError, UnsupportedUrl, ValueError) as exc:
        print({"error": type(exc).__name__})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _safe_command_output(value: HighlightCollection | HighlightStoryPage) -> dict[str, object]:
    if isinstance(value, HighlightCollection):
        return value.safe_summary()
    return value.safe_summary()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in TikTok Highlights Apify discovery POC")
    parser.add_argument("--mode", choices=("highlight_list", "highlight_posts"), required=True)
    parser.add_argument("--username")
    parser.add_argument("--collection-id")
    parser.add_argument("--posts-per-page", type=int, default=10)
    parser.add_argument("--max-charge-usd", type=float, required=True)
    parser.add_argument("--live", action="store_true", help="Authorize one paid, metadata-only Actor run")
    args = parser.parse_args(argv)
    if args.mode == "highlight_list" and not args.username:
        parser.error("--username is required for highlight_list")
    if args.mode == "highlight_posts" and not args.collection_id:
        parser.error("--collection-id is required for highlight_posts")

    # Dry run validates the documented payload shape without reading a token or network.
    try:
        if not args.live:
            payload = (
                ApifyTikTokHighlightsPoc.discovery_input(args.username)
                if args.mode == "highlight_list"
                else ApifyTikTokHighlightsPoc.stories_input(args.collection_id, posts_per_page=args.posts_per_page)
            )
            print({"dry_run": True, "actor": ACTOR_ID, "payload": payload, "max_charge_usd": args.max_charge_usd})
            return 0
        client = ApifyTikTokHighlightsPoc(os.getenv(TOKEN_ENV), max_charge_usd=args.max_charge_usd)
        print({"warning": "paid_metadata_only_run", "max_charge_usd": args.max_charge_usd})
        result: HighlightCollection | HighlightStoryPage
        if args.mode == "highlight_list":
            collections = asyncio.run(client.discover(args.username, live_authorized=True))
            print({"collection_count": len(collections), "collections": [_safe_command_output(value) for value in collections]})
        else:
            result = asyncio.run(client.stories(args.collection_id, posts_per_page=args.posts_per_page, live_authorized=True))
            print(_safe_command_output(result))
        return 0
    except (TikTokHighlightsPocError, UnsupportedUrl, ValueError) as exc:
        print({"error": type(exc).__name__})
        return 2


if __name__ == "__main__":  # pragma: no cover - command wrapper
    raise SystemExit(main())
