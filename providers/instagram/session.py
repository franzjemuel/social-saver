import asyncio
from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, FeedbackRequired, PleaseWaitFewMinutes, LoginRequired
from core.config import settings
from core.provider_sessions import ProviderSessionStore

class InstagramSessionManager:
    LABEL="beta-service"

    def __init__(self,pool):
        self.store=ProviderSessionStore(pool,settings.session_master_key)

    async def client(self):
        if not settings.instagram_session_username:
            return None
        row=await self.store.get_or_create("instagram",self.LABEL)
        usable=await self.store.usable("instagram",self.LABEL)
        cl=Client()
        if usable:
            _,saved=usable
            cl.set_settings(saved)
            return cl
        if not settings.instagram_session_password:
            return None
        try:
            await asyncio.to_thread(cl.login,settings.instagram_session_username,settings.instagram_session_password)
            await self.store.save_healthy(row["id"],cl.get_settings())
            return cl
        except ChallengeRequired:
            await self.store.mark(row["id"],"challenge","CHALLENGE_REQUIRED")
        except (PleaseWaitFewMinutes,FeedbackRequired):
            await self.store.mark(row["id"],"cooldown","RATE_OR_FEEDBACK_BLOCK",60)
        except LoginRequired:
            await self.store.mark(row["id"],"needs_login","LOGIN_REQUIRED")
        except Exception as exc:
            await self.store.mark(row["id"],"cooldown",type(exc).__name__,30)
        return None
