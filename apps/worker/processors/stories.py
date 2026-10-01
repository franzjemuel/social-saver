from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import settings
from core.media_download import download_asset
from core.telegram_delivery import TelegramDelivery
from providers.base import MediaNotFound
from providers.instagram.apify_stories import ApifyInstagramStoriesProvider


async def process_resolve_stories(job, repo, bot):
    target = (job["input"] or {}).get("target")
    provider = ApifyInstagramStoriesProvider(
        settings.apify_api_token,
        settings.apify_stories_actor_id,
        settings.apify_max_total_charge_usd,
        settings.apify_story_limit,
    )
    stories = await provider.resolve(target or "")
    if not stories:
        raise MediaNotFound("No active public Stories were found for this account")

    files = []
    with TemporaryDirectory(prefix=f"social-saver-stories-{job['id']}-") as tmp:
        root = Path(tmp)
        for position, story in enumerate(stories):
            await repo.upsert_resolved_media(story)
            asset = story.assets[0]
            suffix = ".mp4" if asset.asset_type == "video" else ".jpg"
            path = root / f"{position:03d}{suffix}"
            await download_asset(asset.source_url, path)
            files.append((asset.asset_type, path))
        messages = await TelegramDelivery(bot).send_files(
            job["telegram_chat_id"], files, caption=f"@{stories[0].creator_username} • active Stories"
        )
    return {
        "platform": "instagram",
        "media_type": "stories",
        "asset_count": len(files),
        "strategy": "apify_public_stories_v1",
        "telegram_message_ids": [message.message_id for message in messages],
    }
