from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

@dataclass
class ResolvedAsset:
    position: int
    asset_type: str
    source_url: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None

@dataclass
class ResolvedMedia:
    platform: str
    platform_media_id: str
    canonical_url: str
    media_type: str
    assets: list[ResolvedAsset]
    creator_username: str | None = None
    caption: str | None = None
    published_at: datetime | None = None
    strategy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class ProviderError(RuntimeError): pass
class TerminalProviderError(ProviderError): pass
class RetryableProviderError(ProviderError): pass

class UnsupportedUrl(TerminalProviderError): pass
class MediaNotFound(TerminalProviderError): pass
class LoginRequired(TerminalProviderError): pass
class RateLimited(RetryableProviderError): pass
class SourceUnavailable(RetryableProviderError): pass

class MediaProvider(ABC):
    @abstractmethod
    def supports(self, url: str) -> bool: ...
    @abstractmethod
    async def resolve(self, url: str) -> ResolvedMedia: ...


@dataclass
class DiscoveredMedia:
    platform_media_id: str
    canonical_url: str
    published_at: datetime | None = None
    content_kind: str = "post"
    metadata: dict[str, Any] = field(default_factory=dict)

class WatchProvider(ABC):
    @abstractmethod
    async def resolve_target(self, target: str) -> tuple[str, str]: ...
    @abstractmethod
    async def discover(self, target_key: str, cursor: dict[str, Any], limit: int = 12) -> tuple[list[DiscoveredMedia], dict[str, Any]]: ...


@dataclass(frozen=True)
class ArchiveMediaAsset:
    """A provider-neutral media asset belonging to a mirrored profile post.

    ``source_url`` is intentionally only an ephemeral acquisition hint. Long-lived
    archive bytes are stored by the worker, never by a provider implementation.
    """
    position: int
    asset_type: str
    source_url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngagementSnapshot:
    observed_at: datetime
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    share_count: int | None = None
    save_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchivedProfile:
    platform: str
    platform_account_id: str
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchivedPost:
    platform_post_id: str
    original_url: str
    media_type: str
    assets: list[ArchiveMediaAsset]
    caption: str | None = None
    published_at: datetime | None = None
    thumbnail_url: str | None = None
    engagement: EngagementSnapshot | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchivedHighlightStoryMembership:
    """Provider-neutral, non-expiring metadata for one Highlight Story.

    Provider URLs and raw responses are intentionally absent. A later
    worker-owned acquisition phase may resolve ephemeral descriptors in memory.
    """

    provider_namespace: str
    provider_item_id: str
    provider_position: int
    media_type: str
    caption: str | None = None
    published_at: datetime | None = None
    duration_seconds: float | None = None
    has_media_descriptor: bool = False
    has_cover_descriptor: bool = False


@dataclass(frozen=True)
class ArchivedHighlightCollectionSnapshot:
    """One validated provider snapshot, preserving observed—not asserted—order."""

    provider_namespace: str
    provider_collection_id: str
    title: str | None
    reported_item_count: int | None
    has_cover_descriptor: bool
    observation_complete: bool
    memberships: tuple[ArchivedHighlightStoryMembership, ...]


class ProfileArchiveProvider(ABC):
    """Discovers and normalizes profile metadata without downloading media."""

    @abstractmethod
    async def discover_profile(self, target: str, *, limit: int = 12) -> tuple[ArchivedProfile, list[ArchivedPost]]: ...
