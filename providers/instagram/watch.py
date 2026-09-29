import asyncio
from instagrapi import Client
from providers.base import DiscoveredMedia, SourceUnavailable

class InstagramWatchProvider:
    platform="instagram"
    def __init__(self, pool=None):
        self.pool=pool
        self.client=Client()
        self.client.public_transport="curl"

    async def resolve_target(self,target):
        username=target.strip().lstrip("@").split("?")[0].strip("/")
        if "instagram.com/" in username:
            username=username.split("instagram.com/",1)[1].split("/",1)[0]
        if not username: raise ValueError("Missing Instagram username")
        try:
            info=await asyncio.to_thread(self.client.user_info_by_username_gql,username)
            return str(info.pk),username
        except Exception as exc:
            raise SourceUnavailable(f"Could not resolve @{username}") from exc

    async def discover(self,target_key,cursor,limit=12):
        try:
            medias=await asyncio.to_thread(self.client.user_medias_gql,target_key,limit)
        except Exception as public_exc:
            if not self.pool:
                raise SourceUnavailable("Instagram profile discovery temporarily failed") from public_exc
            from providers.instagram.session import InstagramSessionManager
            cl=await InstagramSessionManager(self.pool).client()
            if not cl:
                raise SourceUnavailable("Instagram public discovery failed and no authenticated session is healthy") from public_exc
            try:
                medias=await asyncio.to_thread(cl.user_medias,target_key,limit)
            except Exception as private_exc:
                raise SourceUnavailable("Instagram authenticated profile discovery failed") from private_exc
        previous=set(cursor.get("recent_ids",[]))
        found=[]
        ids=[]
        for m in medias:
            mid=str(m.pk); ids.append(mid)
            if mid not in previous:
                found.append(DiscoveredMedia(mid,f"https://www.instagram.com/p/{m.code}/",getattr(m,"taken_at",None)))
        # newest-first baseline: first successful poll establishes state, not a historical flood
        if not cursor.get("initialized"):
            found=[]
        return found,{"initialized":True,"recent_ids":ids[:50]}
