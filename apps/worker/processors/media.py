from pathlib import Path
from tempfile import TemporaryDirectory
from providers.router import ProviderRouter
from core.media_download import download_asset
from core.telegram_delivery import TelegramDelivery
from core.delivery_router import DeliveryRouter
from core.archive import ArchiveService

EXT = {"photo": ".jpg", "video": ".mp4"}

async def process_resolve_media(job, repo, bot, pool=None):
    url = (job["input"] or {}).get("url")
    if not url:
        raise ValueError("Missing job input URL")

    provider = ProviderRouter(pool).for_url(url)
    media = await provider.resolve(url)
    media_item_id = await repo.upsert_resolved_media(media)
    router = DeliveryRouter()
    telegram_files = []
    overflow_links = []
    archive_requested = bool((job["input"] or {}).get("archive"))
    auto_delivery = bool((job["input"] or {}).get("auto_delivery", True))
    archive_results = []

    with TemporaryDirectory(prefix=f"social-saver-{job['id']}-") as tmp:
        root = Path(tmp)
        for asset in media.assets:
            suffix = EXT.get(asset.asset_type, ".bin")
            path = root / f"{asset.position:03d}{suffix}"
            downloaded = await download_asset(asset.source_url, path)
            decision = await router.decide(
                downloaded,
                platform=media.platform,
                media_id=media.platform_media_id,
                position=asset.position,
            )
            await repo.update_asset_storage(
                media_item_id, asset.position,
                size_bytes=downloaded.size_bytes,
                sha256=downloaded.sha256,
                storage_provider="r2" if decision.route == "r2_link" else None,
                storage_key=decision.storage_key,
            )
            if archive_requested:
                media_asset = await repo.get_media_asset(media_item_id, asset.position)
                archived = await ArchiveService(repo).archive_download(
                    user_id=job["user_id"], media_item_id=media_item_id,
                    media_asset_id=media_asset["id"], downloaded=downloaded,
                    platform=media.platform, media_id=media.platform_media_id, suffix=suffix
                )
                archive_results.append({
                    "position": asset.position,
                    "archive_entry_id": archived.archive_entry_id,
                    "reused_object": archived.reused_object,
                })
            if decision.route == "telegram":
                telegram_files.append((asset.asset_type, path))
            else:
                overflow_links.append((asset.position, decision.url))

        label = f"@{media.creator_username}" if media.creator_username else "Instagram"
        caption = f"{label} • {media.media_type}"
        message_ids = []

        if telegram_files and auto_delivery:
            delivery = TelegramDelivery(bot)
            messages = await delivery.send_files(job["telegram_chat_id"], telegram_files, caption=caption)
            message_ids.extend(m.message_id for m in messages)

        if overflow_links and auto_delivery:
            lines = ["📦 Original file is too large for Telegram's hosted bot upload limit."]
            for position, url in overflow_links:
                lines.append(f"Item {position + 1}: {url}")
            lines.append("Link expires automatically.")
            msg = await bot.send_message(job["telegram_chat_id"], "\n".join(lines), disable_web_page_preview=True)
            message_ids.append(msg.message_id)

        return {
            "media_item_id": str(media_item_id),
            "platform": media.platform,
            "media_type": media.media_type,
            "asset_count": len(media.assets),
            "strategy": media.strategy,
            "telegram_message_ids": message_ids,
            "overflow_count": len(overflow_links),
            "archive_requested": archive_requested,
            "archive_assets": archive_results,
        }
