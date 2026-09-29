import asyncio
from providers.base import DiscoveredMedia, SourceUnavailable
from providers.instagram.session import InstagramSessionManager

class InstagramStoryDiscovery:
    platform="instagram"
    def __init__(self,pool): self.pool=pool

    async def discover(self,target_key,cursor,limit=25):
        cl=await InstagramSessionManager(self.pool).client()
        if not cl:
            raise SourceUnavailable("Authenticated Instagram session is unavailable")
        try:
            stories=await asyncio.to_thread(cl.user_stories,target_key,limit)
        except Exception as exc:
            raise SourceUnavailable("Instagram Story discovery failed") from exc
        previous=set(cursor.get("recent_story_ids",[]))
        ids=[str(s.pk) for s in stories]
        found=[
          DiscoveredMedia(str(s.pk),f"https://www.instagram.com/stories/{s.user.username}/{s.pk}/",s.taken_at,"story")
          for s in stories if str(s.pk) not in previous
        ]
        if not cursor.get("stories_initialized"): found=[]
        return found,{"stories_initialized":True,"recent_story_ids":ids[:100]}
