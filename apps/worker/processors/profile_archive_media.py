from pathlib import Path
from tempfile import TemporaryDirectory

from core.profile_archive_media import ProfileArchiveMediaService
from providers.tiktok.download import TikTokMediaDownloader


async def process_archive_profile_media(job, repo, downloader=None, media_service=None):
    """Persist supported profile video assets; photo/carousel rows remain untouched."""
    profile_id = (job["input"] or {}).get("profile_id")
    if not profile_id:
        raise ValueError("Missing archived profile id")
    assets = await repo.list_owned_archived_video_assets(job["user_id"], profile_id)
    downloader = downloader or TikTokMediaDownloader()
    media_service = media_service or ProfileArchiveMediaService(repo)
    persisted = skipped = failures = 0
    with TemporaryDirectory(prefix=f"social-saver-profile-{job['id']}-") as tmp:
        root = Path(tmp)
        for asset in assets:
            if asset["asset_type"] != "video":
                skipped += 1
                continue
            try:
                downloaded = await downloader.download_post(asset["original_url"], root / f"{asset['id']}.mp4")
                reused = await media_service.persist(
                    user_id=job["user_id"], profile_id=profile_id, asset_id=asset["id"], downloaded=downloaded,
                )
                persisted += 1
                skipped += int(reused)
            except Exception:
                failures += 1
    return {"profile_id": str(profile_id), "persisted": persisted, "skipped": skipped, "failures": failures}
