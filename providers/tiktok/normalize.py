"""Pure yt-dlp-to-Social-Saver metadata normalization."""

from datetime import datetime, timezone
from typing import Any

from providers.base import (
    ArchiveMediaAsset,
    ArchivedPost,
    ArchivedProfile,
    EngagementSnapshot,
    SourceUnavailable,
)


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _url(value: Any) -> str | None:
    value = _text(value)
    return value if value and value.startswith(("https://", "http://")) else None


def _published_at(metadata: dict[str, Any]) -> datetime | None:
    timestamp = metadata.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    upload_date = _text(metadata.get("upload_date"))
    if upload_date:
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def normalize_tiktok_profile(metadata: dict[str, Any], username: str) -> ArchivedProfile:
    """Return the stable TikTok account identity from profile/entry metadata."""
    account_id = _text(metadata.get("uploader_id") or metadata.get("channel_id"))
    if not account_id:
        raise SourceUnavailable("TikTok metadata did not include a stable account ID")
    resolved_username = _text(metadata.get("uploader") or metadata.get("channel") or username) or username
    return ArchivedProfile(
        platform="tiktok",
        platform_account_id=account_id,
        username=resolved_username.lstrip("@"),
        display_name=_text(metadata.get("uploader") or metadata.get("channel")),
        bio=_text(metadata.get("description")) if metadata.get("_type") == "playlist" else None,
        avatar_url=_url(metadata.get("thumbnail") or metadata.get("uploader_url")),
        metadata={
            "extractor": _text(metadata.get("extractor")),
            "channel_id": _text(metadata.get("channel_id")),
        },
    )


def normalize_tiktok_post(metadata: dict[str, Any], *, observed_at: datetime | None = None) -> ArchivedPost:
    """Normalize one yt-dlp TikTok entry; retain only controlled provider detail."""
    post_id = _text(metadata.get("id"))
    original_url = _url(metadata.get("webpage_url") or metadata.get("original_url"))
    if not post_id or not original_url:
        raise SourceUnavailable("TikTok metadata did not include a post ID and original URL")
    observed_at = observed_at or datetime.now(timezone.utc)
    duration = metadata.get("duration")
    duration_seconds = float(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
    thumbnail = _url(metadata.get("thumbnail"))
    media_url = _url(metadata.get("url"))
    return ArchivedPost(
        platform_post_id=post_id,
        original_url=original_url,
        media_type="video",
        caption=_text(metadata.get("description") or metadata.get("title")),
        published_at=_published_at(metadata),
        thumbnail_url=thumbnail,
        assets=[ArchiveMediaAsset(
            position=0,
            asset_type="video",
            source_url=media_url,
            thumbnail_url=thumbnail,
            duration_seconds=duration_seconds,
            metadata={"format_id": _text(metadata.get("format_id"))},
        )],
        engagement=EngagementSnapshot(
            observed_at=observed_at,
            view_count=_count(metadata.get("view_count")),
            like_count=_count(metadata.get("like_count")),
            comment_count=_count(metadata.get("comment_count")),
            repost_count=_count(metadata.get("repost_count")),
            share_count=_count(metadata.get("share_count")),
            save_count=_count(metadata.get("save_count") or metadata.get("bookmark_count")),
        ),
        metadata={
            "extractor": _text(metadata.get("extractor")),
            "upload_date": _text(metadata.get("upload_date")),
        },
    )


def normalize_scanned_tiktok_post(post_id: str, canonical_url: str, *, is_photo: bool, caption: str | None, error: str) -> ArchivedPost:
    """Keep a scanner-discovered post when optional rich metadata fails."""
    return ArchivedPost(
        platform_post_id=post_id,
        original_url=canonical_url,
        media_type="photo" if is_photo else "video",
        assets=[],
        caption=caption,
        metadata={"metadata_resolution_error": error},
    )
