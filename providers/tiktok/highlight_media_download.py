"""Safe acquisition seam for transient TikTok Highlight media descriptors.

The production Apify provider is intentionally not enabled. This module keeps
the worker boundary explicit: synthetic tests can inject verified media, photo
descriptors use the existing hardened image downloader, and video descriptors
use the separately bounded, pinned-IP MP4 transport.
"""

from pathlib import Path

from providers.base import HighlightStoryMediaDescriptor, SourceUnavailable
from providers.tiktok.image_download import TikTokImageDownloader
from providers.tiktok.highlight_video_download import TikTokHighlightMp4Downloader


class TikTokHighlightMediaDownloader:
    def __init__(self, *, image_downloader=None, video_downloader=None):
        self._image_downloader = image_downloader or TikTokImageDownloader()
        self._video_downloader = video_downloader or TikTokHighlightMp4Downloader()

    async def download(self, descriptor: HighlightStoryMediaDescriptor, destination: Path):
        if descriptor.asset_type == "photo":
            return await self._image_downloader.download_asset(descriptor.candidates, destination)
        if descriptor.asset_type == "video":
            return await self._video_downloader.download(descriptor.candidates, destination)
        raise SourceUnavailable("TikTok Highlight media type is unsupported")
