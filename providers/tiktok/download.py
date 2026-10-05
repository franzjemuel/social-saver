from pathlib import Path

from providers.base import ArchiveMediaAsset


class TikTokMediaDownloader:
    """Explicit future seam for worker-owned TikTok media acquisition.

    Profile metadata imports intentionally do not invoke this interface.
    """

    async def download(self, asset: ArchiveMediaAsset, destination: Path):
        raise NotImplementedError("TikTok media download is not part of the metadata import increment")
