from datetime import datetime, timezone
import re

import httpx

from providers.base import ResolvedAsset, ResolvedMedia, SourceUnavailable, UnsupportedUrl


USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_public_profile(target: str) -> str:
    value = target.strip().lstrip("@").split("?", 1)[0].rstrip("/")
    if "instagram.com/" in value:
        value = value.split("instagram.com/", 1)[1].split("/", 1)[0]
    if not USERNAME.fullmatch(value):
        raise UnsupportedUrl("Send a public Instagram username or profile link")
    return value


class ApifyInstagramStoriesProvider:
    """Resolve active public Stories without an Instagram account session."""

    def __init__(self, token: str | None, actor_id: str, max_charge_usd: float = 0.05, limit: int = 10):
        self.token = token
        self.actor_id = actor_id
        self.max_charge_usd = max_charge_usd
        self.limit = limit

    async def resolve(self, target: str) -> list[ResolvedMedia]:
        username = normalize_public_profile(target)
        if not self.token:
            raise SourceUnavailable("Public Story downloads are not configured yet")
        endpoint = f"https://api.apify.com/v2/actors/{self.actor_id}/run-sync-get-dataset-items"
        params = {
            "clean": "true",
            "format": "json",
            "limit": str(self.limit),
            "maxItems": str(self.limit),
            "maxTotalChargeUsd": str(self.max_charge_usd),
            "timeout": "120",
        }
        try:
            async with httpx.AsyncClient(timeout=150, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    params=params,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"targets": [username], "scrapeType": "stories"},
                )
                response.raise_for_status()
                rows = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceUnavailable(f"Public Story provider failed: {type(exc).__name__}") from exc
        if not isinstance(rows, list):
            raise SourceUnavailable("Public Story provider returned an invalid response")

        resolved = []
        for row in rows[: self.limit]:
            if not isinstance(row, dict) or row.get("item_type") not in {None, "story"}:
                continue
            video_url = row.get("video_url")
            image_url = row.get("image_url")
            source_url = video_url or image_url
            if not isinstance(source_url, str) or not source_url.startswith("https://"):
                continue
            story_id = str(row.get("story_id") or row.get("id") or len(resolved))
            taken_at = row.get("taken_at")
            published_at = None
            if isinstance(taken_at, (int, float)):
                published_at = datetime.fromtimestamp(taken_at, tz=timezone.utc)
            is_video = bool(video_url) or row.get("media_type") == "video"
            resolved.append(ResolvedMedia(
                platform="instagram",
                platform_media_id=story_id,
                canonical_url=f"https://www.instagram.com/stories/{username}/{story_id}/",
                media_type="story_video" if is_video else "story_photo",
                assets=[ResolvedAsset(
                    position=0,
                    asset_type="video" if is_video else "photo",
                    source_url=source_url,
                    duration_seconds=row.get("video_duration"),
                )],
                creator_username=str(row.get("source_username") or username),
                published_at=published_at,
                strategy="apify_public_stories_v1",
                metadata={"content_kind": "story"},
            ))
        return resolved
