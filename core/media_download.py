from dataclasses import dataclass
from pathlib import Path
import hashlib
import httpx

@dataclass(frozen=True)
class DownloadedAsset:
    path: Path
    size_bytes: int
    sha256: str
    content_type: str | None

async def download_asset(url: str, destination: Path, max_bytes: int = 2_000_000_000) -> DownloadedAsset:
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    digest = hashlib.sha256()
    content_type = None
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type")
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise ValueError("Asset exceeds configured ingestion size limit")
            with destination.open("wb") as f:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Asset exceeds configured ingestion size limit")
                    digest.update(chunk)
                    f.write(chunk)
    return DownloadedAsset(destination, total, digest.hexdigest(), content_type)
