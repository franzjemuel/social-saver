from pathlib import Path

from providers.base import ArchiveMediaAsset
from providers.base import SourceUnavailable
from providers.tiktok.metadata import TikTokPostMetadataResolver
from core.media_download import download_asset


class TikTokMediaDownloader:
    """Explicit future seam for worker-owned TikTok media acquisition.

    Profile metadata imports intentionally do not invoke this interface.
    """

    def __init__(self, metadata_resolver=None, downloader=download_asset):
        self.metadata_resolver = metadata_resolver or TikTokPostMetadataResolver()
        self._downloader = downloader

    async def download_post(self, canonical_url: str, destination: Path):
        """Refresh a concrete public post before downloading its video bytes."""
        metadata = await self.metadata_resolver.resolve(canonical_url)
        source_url = metadata.get("url")
        if not isinstance(source_url, str) or not source_url.startswith(("https://", "http://")):
            raise SourceUnavailable("TikTok post has no downloadable video URL")
        return await self._downloader(source_url, destination)
