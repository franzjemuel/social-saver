from providers.live import LiveSource
from providers.instagram.session import InstagramSessionManager
class InstagramLiveProbe:
    """Experimental only. A top-live miss is unknown, never proof the target is offline."""
    def __init__(self,pool): self.pool=pool
    async def probe_top_live(self,target_key):
        cl=await InstagramSessionManager(self.pool).client()
        if not cl: return {"state":"session_unavailable"}
        try: data=cl.private_request("discover/top_live/")
        except Exception as exc: return {"state":"error","error":type(exc).__name__}
        for b in data.get("broadcasts",[]):
            owner=b.get("broadcast_owner") or {}
            if str(owner.get("pk") or owner.get("id"))==str(target_key):
                manifest=b.get("dash_abr_playback_url") or b.get("dash_playback_url")
                if manifest: return {"state":"live","source":LiveSource("instagram",str(target_key),str(b.get("id")),manifest,{"User-Agent":getattr(cl,"user_agent","")})}
                return {"state":"live_no_manifest"}
        return {"state":"unknown"}
