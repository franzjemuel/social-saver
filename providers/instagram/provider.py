import asyncio
from instagrapi import Client
from providers.base import (MediaProvider, ResolvedMedia, ResolvedAsset,
                            UnsupportedUrl, SourceUnavailable)

from providers.instagram.urls import normalize_instagram_url

class InstagramProvider(MediaProvider):
    def supports(self, url: str) -> bool:
        try:
            normalize_instagram_url(url); return True
        except UnsupportedUrl:
            return False

    async def resolve(self, url: str) -> ResolvedMedia:
        canonical = normalize_instagram_url(url)
        return await asyncio.to_thread(self._resolve_sync, canonical)

    def _resolve_sync(self, canonical: str) -> ResolvedMedia:
        # Public/web-first. No customer credentials are used in Beta A.
        client = Client(public_transport="curl", public_transport_impersonate="chrome136")
        try:
            pk = client.media_pk_from_url(canonical)
            media = client.media_info_gql(pk)
        except Exception as exc:
            raise SourceUnavailable(f"Instagram public resolver failed: {type(exc).__name__}") from exc

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
            strategy="instagrapi_public_gql", metadata={"shortcode": media.code}
        )
