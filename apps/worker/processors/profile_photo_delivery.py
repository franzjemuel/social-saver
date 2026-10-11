from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import settings
from core.storage import R2Storage
from core.telegram_delivery import TelegramDelivery
from providers.base import TerminalProviderError


class ProfilePhotoDeliveryFailure(TerminalProviderError):
    """A safe, terminal result for a user-requested Telegram photo delivery."""

    def __init__(self):
        super().__init__("Archived photos could not be sent to Telegram")


class ProfilePhotoDeliveryUnconfirmed(ProfilePhotoDeliveryFailure):
    """A previous Telegram request may have been accepted, but lacks a receipt."""

    notify_user = False

    def __init__(self):
        TerminalProviderError.__init__(self, "Archived photo delivery could not be confirmed")


async def process_deliver_profile_photos(job, repo, bot, storage=None, delivery=None):
    """Deliver one complete persisted photo set to the verified owner's bot DM."""
    payload = job["input"] or {}
    profile_id = payload.get("profile_id")
    post_id = payload.get("post_id")
    expected_count = payload.get("asset_count")
    if not profile_id or not post_id or not isinstance(expected_count, int):
        raise ProfilePhotoDeliveryFailure()
    assets = await repo.list_owned_complete_profile_photo_delivery_assets(
        job["user_id"], profile_id, post_id,
    )
    if len(assets) != expected_count or not 1 <= len(assets) <= 10:
        raise ProfilePhotoDeliveryFailure()
    allowed_types = {"image/jpeg", "image/png"}
    if any(
        asset["content_type"] not in allowed_types
        or not isinstance(asset["size_bytes"], int)
        or asset["size_bytes"] < 1
        or asset["size_bytes"] > settings.telegram_photo_upload_limit_bytes
        for asset in assets
    ):
        raise ProfilePhotoDeliveryFailure()
    storage = storage or R2Storage(
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.r2_bucket,
        settings.r2_presign_seconds,
    )
    delivery = delivery or TelegramDelivery(bot)
    with TemporaryDirectory(prefix=f"social-saver-photo-delivery-{job['id']}-") as directory:
        root = Path(directory)
        files = []
        try:
            for index, asset in enumerate(assets):
                suffix = ".jpg" if asset["content_type"] == "image/jpeg" else ".png"
                path = root / f"{index:02d}{suffix}"
                await storage.download_file(asset["storage_key"], path)
                if not path.is_file() or path.stat().st_size < 1:
                    raise ProfilePhotoDeliveryFailure()
                files.append(("photo", path))
            attempt = await repo.claim_profile_photo_delivery_attempt(
                job["id"], job["user_id"], profile_id, post_id,
            )
            if attempt == "confirmed":
                return {"sent": len(files), "batches": 1, "recovered": True}
            if attempt != "claimed":
                # A timeout/crash after a prior call can be indistinguishable
                # from Telegram accepting it. Never resend automatically.
                raise ProfilePhotoDeliveryUnconfirmed()
            try:
                await delivery.send_files(job["telegram_chat_id"], files)
            except Exception:
                raise ProfilePhotoDeliveryUnconfirmed() from None
            try:
                confirmed = await repo.confirm_profile_photo_delivery_attempt(
                    job["id"], job["user_id"], profile_id, post_id,
                )
            except Exception:
                raise ProfilePhotoDeliveryUnconfirmed() from None
            if not confirmed:
                raise ProfilePhotoDeliveryUnconfirmed()
        except ProfilePhotoDeliveryFailure:
            raise
        except Exception:
            # R2 and Telegram exceptions can contain credentials, keys, or URLs.
            raise ProfilePhotoDeliveryFailure() from None
    return {"sent": len(files), "batches": 1}
