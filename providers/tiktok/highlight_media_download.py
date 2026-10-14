"""Safe acquisition seam for transient TikTok Highlight media descriptors.

The production Apify provider is intentionally not enabled. This module keeps
the worker boundary explicit: synthetic tests can inject verified media, photo
descriptors use the existing hardened image downloader, and video descriptors
fail closed until a separately reviewed pinned-IP MP4 transport is introduced.
"""

from pathlib import Path

from providers.base import HighlightStoryMediaDescriptor, SourceUnavailable
from providers.tiktok.image_download import TikTokImageDownloader


class TikTokHighlightMediaDownloader:
    def __init__(self, *, image_downloader=None, video_downloader=None):
        self._image_downloader = image_downloader or TikTokImageDownloader()
        self._video_downloader = video_downloader

    async def download(self, descriptor: HighlightStoryMediaDescriptor, destination: Path):
        if descriptor.asset_type == "photo":
            return await self._image_downloader.download_asset(descriptor.candidates, destination)
        if descriptor.asset_type == "video" and self._video_downloader is not None:
            return await self._video_downloader.download(descriptor.candidates, destination)
        # Never fall back to an unconstrained HTTP client for a provider-supplied
        # descriptor. The future live phase must supply a reviewable MP4
        # downloader with the image downloader's SSRF/redirect guarantees.
        raise SourceUnavailable("TikTok Highlight video acquisition is not enabled")
