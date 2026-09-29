from dataclasses import dataclass
from pathlib import Path
from core.config import settings
from core.storage import R2Storage

@dataclass(frozen=True)
class DeliveryDecision:
    route: str  # telegram | r2_link
    url: str | None = None
    storage_key: str | None = None

class DeliveryRouter:
    def __init__(self):
        self.limit = settings.telegram_hosted_upload_limit_bytes

    async def decide(self, downloaded, *, platform: str, media_id: str, position: int) -> DeliveryDecision:
        if downloaded.size_bytes <= self.limit:
            return DeliveryDecision("telegram")

        storage = R2Storage(
            settings.r2_account_id,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
            settings.r2_bucket,
            settings.r2_presign_seconds,
        )
        suffix = downloaded.path.suffix.lower()
        key = f"overflow/{platform}/{media_id}/{downloaded.sha256}{suffix}"
        await storage.put_file(downloaded.path, key, downloaded.content_type)
        url = await storage.presigned_get(key)
        return DeliveryDecision("r2_link", url=url, storage_key=key)
