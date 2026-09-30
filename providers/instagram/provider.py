import asyncio
from instagrapi import Client
from providers.base import (MediaProvider, ResolvedMedia, ResolvedAsset,
                            UnsupportedUrl, SourceUnavailable)

from providers.instagram.urls import normalize_instagram_url
from providers.instagram.session import InstagramSessionManager

class InstagramProvider(MediaProvider):
    def __init__(self, pool=None):
        self.pool = pool

    def supports(self, url: str) -> bool:
        try:
            normalize_instagram_url(url); return True
        except UnsupportedUrl:
            return False

    async def resolve(self, url: str) -> ResolvedMedia:
        canonical = normalize_instagram_url(url)
        try:
            return await asyncio.to_thread(self._resolve_sync, canonical)
        except SourceUnavailable:
            if self.pool is None:
                raise
        manager = InstagramSessionManager(self.pool)
        client = await manager.client()
        if client is None:
            raise SourceUnavailable("Instagram authenticated session is unavailable or needs attention")
        try:
            media = await asyncio.to_thread(self._authenticated_media, client, canonical)
        except Exception as exc:
            await manager.record_failure(exc)
            raise SourceUnavailable(f"Instagram authenticated resolver failed: {type(exc).__name__}") from None
        return self._normalize(media, canonical, "instagrapi_authenticated_v1")

    @staticmethod
    def _authenticated_media(client, canonical):
        return client.media_info_v1(client.media_pk_from_url(canonical))

    def _resolve_sync(self, canonical: str) -> ResolvedMedia:
        # Public/web-first. No customer credentials are used in Beta A.
        client = Client(public_transport="curl", public_transport_impersonate="chrome136")
        try:
            pk = client.media_pk_from_url(canonical)
            media = client.media_info_gql(pk)
        except Exception as exc:
            raise SourceUnavailable(f"Instagram public resolver failed: {type(exc).__name__}") from exc

        return self._normalize(media, canonical, "instagrapi_public_gql")

    @staticmethod
    def _normalize(media, canonical, strategy):
        resources = list(media.resources or []) if media.media_type == 8 else [media]
        assets = []
        for i, item in enumerate(resources):
            if item.media_type == 2 and item.video_url:
                kind, source = "video", str(item.video_url)
            else:
                kind, source = "photo", str(item.thumbnail_url)
            assets.append(ResolvedAsset(position=i, asset_type=kind, source_url=source))

        if media.media_type == 8: media_type = "carousel"
        elif media.media_type == 2 and media.product_type == "clips": media_type = "reel"
        elif media.media_type == 2: media_type = "video"
        else: media_type = "photo"
        caption = getattr(getattr(media, "caption_text", None), "text", None) or getattr(media, "caption_text", None)
        return ResolvedMedia(
            platform="instagram", platform_media_id=str(media.pk), canonical_url=canonical,
            media_type=media_type, assets=assets,
            creator_username=getattr(media.user, "username", None),
            caption=str(caption) if caption else None, published_at=media.taken_at,
            strategy=strategy, metadata={"shortcode": media.code}
        )
