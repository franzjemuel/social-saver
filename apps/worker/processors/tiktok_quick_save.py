"""Worker-owned, single-video TikTok quick-save delivery.

Native MP4 files go straight to the requester's Telegram chat, without any
profile import, paid Apify provider, or persistent object-store write.
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import settings
from core.telegram_delivery import TelegramDelivery
from providers.base import MediaNotFound, SourceUnavailable, TerminalProviderError
from providers.tiktok.download import TikTokMediaDownloader
from providers.tiktok.quick_save import normalize_tiktok_quick_save_url, resolve_tiktok_quick_save_url


class TikTokQuickSaveFailure(TerminalProviderError):
    def __init__(self, message="This TikTok video could not be sent. Please try another public video."):
        super().__init__(message)


class TikTokQuickSaveUnconfirmed(TikTokQuickSaveFailure):
    """A Telegram send might have succeeded. Never resend it automatically."""
    notify_user = False

    def __init__(self):
        TerminalProviderError.__init__(self, "TikTok video delivery could not be confirmed")


async def process_tiktok_quick_save(job, repo, bot, *, downloader=None, delivery=None, resolver=None):
    payload = job["input"] or {}
    try:
        pending_url = normalize_tiktok_quick_save_url(payload.get("url"))
        # Short URLs are dereferenced in the worker, never the bot.
        # The resolver enforces exact TikTok hosts and bounded redirect hops.
        url = await (resolver or resolve_tiktok_quick_save_url)(pending_url)
        url = normalize_tiktok_quick_save_url(url)
        if "/video/" not in url:
            raise TikTokQuickSaveFailure()
    except (TerminalProviderError, SourceUnavailable):
        raise TikTokQuickSaveFailure("TikTok share link could not be resolved to a public video") from None
    except Exception:
        raise TikTokQuickSaveFailure() from None
    downloader = downloader or TikTokMediaDownloader(max_bytes=settings.telegram_hosted_upload_limit_bytes)
    delivery = delivery or TelegramDelivery(bot)

    with TemporaryDirectory(prefix="social-saver-tiktok-quick-") as directory:
        output = Path(directory) / "video.mp4"
        try:
            downloaded = await downloader.download_post(url, output)
        except (MediaNotFound, SourceUnavailable):
            raise TikTokQuickSaveFailure() from None
        except Exception:
            raise TikTokQuickSaveFailure() from None

        if (not output.is_file() or downloaded.size_bytes < 1
                or downloaded.size_bytes > settings.telegram_hosted_upload_limit_bytes
                or downloaded.content_type != "video/mp4"):
            raise TikTokQuickSaveFailure("This video is unavailable or too large for direct Telegram delivery")

        # Claim immediately before the external send. PGMQ redelivery can safely
        # repeat acquisition, but cannot resend after a possibly accepted upload.
        if not await repo.claim_tiktok_quick_save_delivery(job["id"], job["user_id"]):
            raise TikTokQuickSaveUnconfirmed()
        try:
            messages = await delivery.send_files(job["telegram_chat_id"], [("video", output)], caption="TikTok • Social Saver")
        except Exception:
            raise TikTokQuickSaveUnconfirmed() from None
        if len(messages) != 1:
            raise TikTokQuickSaveUnconfirmed()
        return {"platform": "tiktok", "media_type": "video", "asset_count": 1,
                "telegram_message_ids": [messages[0].message_id], "strategy": "native_tiktok_quick_save_v1"}
