import asyncio
from urllib.parse import urlparse
from providers.base import MediaProvider, ResolvedMedia, ResolvedAsset, UnsupportedUrl, SourceUnavailable
from providers.instagram.session import InstagramSessionManager

def normalize_story_url(url: str) -> str:
    parsed=urlparse(url.strip())
    host=parsed.netloc.lower().split(":")[0]
    if host not in {"instagram.com","www.instagram.com"}:
        raise UnsupportedUrl("Only instagram.com Story links are supported")
    parts=[p for p in parsed.path.split("/") if p]
    if len(parts)<3 or parts[0]!="stories" or not parts[2].isdigit():
        raise UnsupportedUrl("Send a direct Instagram Story link")
    return f"https://www.instagram.com/stories/{parts[1]}/{parts[2]}/"

class InstagramStoryProvider(MediaProvider):
    def __init__(self,pool): self.pool=pool

    def supports(self,url):
        try: normalize_story_url(url); return True
        except UnsupportedUrl: return False

    async def resolve(self,url):
        canonical=normalize_story_url(url)
        story_pk=int(canonical.rstrip("/").split("/")[-1])
        cl=await InstagramSessionManager(self.pool).client()
        if not cl: raise SourceUnavailable("Authenticated Instagram session is unavailable")
        try:
            story=await asyncio.to_thread(cl.story_info,story_pk)
        except Exception as exc:
            raise SourceUnavailable(f"Instagram Story resolver failed: {type(exc).__name__}") from exc

        if story.media_type==2 and story.video_url:
            asset=ResolvedAsset(0,"video",str(story.video_url),duration_seconds=float(story.video_duration or 0))
            media_type="story_video"
        elif story.thumbnail_url:
            asset=ResolvedAsset(0,"photo",str(story.thumbnail_url))
            media_type="story_photo"
        else:
            raise SourceUnavailable("Instagram Story has no downloadable media URL")

        username=getattr(story.user,"username",None)
        return ResolvedMedia(
            platform="instagram",platform_media_id=str(story.pk),canonical_url=canonical,
            media_type=media_type,assets=[asset],creator_username=username,
            published_at=story.taken_at,strategy="instagrapi_authenticated_story",
            metadata={"content_kind":"story","story_id":str(story.id)}
        )
