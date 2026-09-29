import asyncio
import time
from dataclasses import dataclass
from core.config import settings

@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    remaining: int
    reset: float

class AbuseLimiter:
    # Fast velocity limits only. Paid quotas remain authoritative in Postgres.
    WINDOWS = {
        "free": (6, 10),
        "plus": (20, 10),
        "pro": (40, 10),
    }
    def __init__(self):
        self._local={}
        self._remote=None
        if settings.upstash_redis_rest_url and settings.upstash_redis_rest_token:
            from upstash_redis.asyncio import Redis
            from upstash_ratelimit.asyncio import Ratelimit, SlidingWindow
            redis=Redis(url=settings.upstash_redis_rest_url, token=settings.upstash_redis_rest_token)
            self._remote={
                plan:Ratelimit(redis=redis,limiter=SlidingWindow(max_requests=n,window=window),
                               prefix=f"social-saver:velocity:{plan}")
                for plan,(n,window) in self.WINDOWS.items()
            }

    async def check(self,user_id,plan):
        plan=plan if plan in self.WINDOWS else "free"
        if self._remote:
            r=await self._remote[plan].limit(str(user_id))
            return LimitResult(bool(r.allowed),int(r.remaining),float(r.reset))
        # Single-process development fallback only.
        limit,window=self.WINDOWS[plan]; now=time.time(); key=(plan,str(user_id))
        bucket=[t for t in self._local.get(key,[]) if t>now-window]
        if len(bucket)>=limit:
            self._local[key]=bucket
            return LimitResult(False,0,min(bucket)+window)
        bucket.append(now); self._local[key]=bucket
        return LimitResult(True,limit-len(bucket),now+window)

class ProviderConcurrency:
    def __init__(self):
        self._limits={"instagram":asyncio.Semaphore(settings.provider_instagram_concurrency)}
    def for_platform(self,platform):
        return self._limits.setdefault(platform,asyncio.Semaphore(1))

provider_concurrency=ProviderConcurrency()
