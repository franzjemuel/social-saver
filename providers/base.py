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

class WatchProvider(ABC):
    @abstractmethod
    async def resolve_target(self, target: str) -> tuple[str, str]: ...
    @abstractmethod
    async def discover(self, target_key: str, cursor: dict[str, Any], limit: int = 12) -> tuple[list[DiscoveredMedia], dict[str, Any]]: ...
