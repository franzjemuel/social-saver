from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class LiveSource:
    platform: str
    target_key: str
    source_id: str
    manifest_url: str
    headers: dict[str,str] | None = None

class LiveSourceResolver(Protocol):
    async def resolve_live(self,target_key: str) -> LiveSource | None: ...
