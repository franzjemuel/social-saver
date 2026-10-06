from pathlib import Path
from tempfile import TemporaryDirectory

from core.profile_archive_media import ProfileArchiveMediaService
from providers.base import SourceUnavailable, TerminalProviderError
from providers.tiktok.download import TikTokMediaDownloader


async def process_archive_profile_media(job, repo, downloader=None, media_service=None):
    """Persist supported profile video assets; photo/carousel rows remain untouched."""
    profile_id = (job["input"] or {}).get("profile_id")
    post_id = (job["input"] or {}).get("post_id")
    if not profile_id:
        raise ValueError("Missing archived profile id")
    assets = await repo.list_owned_archived_video_assets(job["user_id"], profile_id, post_id)
    downloader = downloader or TikTokMediaDownloader()
    attached = uploaded = reused = skipped = failures = 0
    total_eligible = sum(asset["asset_type"] == "video" for asset in assets)
    if not assets:
        return {"profile_id": str(profile_id), "post_id": str(post_id) if post_id else None,
                "attached": 0, "uploaded": 0, "reused": 0, "skipped": 1 if post_id else 0,
                "failures": 0, "total_eligible": 0, "completed": 0}
    media_service = media_service or ProfileArchiveMediaService(repo)
    with TemporaryDirectory(prefix=f"social-saver-profile-{job['id']}-") as tmp:
        root = Path(tmp)
        for index, asset in enumerate(assets, start=1):
            if asset["asset_type"] != "video":
                skipped += 1
                continue
            try:
                downloaded = await downloader.download_post(asset["original_url"], root / f"{asset['id']}.mp4")
            except (TerminalProviderError, SourceUnavailable):
                # A concrete post may vanish or become temporarily unavailable.
                # Keep attachments from other posts; job results expose only the
                # aggregate count, never provider output or URLs.
                failures += 1
                continue
            except Exception:
                # Provider exceptions can include ephemeral source URLs.  The
                # worker may report this exception to telemetry, so retain only
                # the stage/category rather than a chained provider error.
                failures += 1
                continue
            try:
                reused_object = await media_service.persist(
                    user_id=job["user_id"], profile_id=profile_id, asset_id=asset["id"], downloaded=downloaded,
                )
                attached += 1
                if reused_object:
                    reused += 1
                else:
                    uploaded += 1
            except SourceUnavailable:
                failures += 1
                continue
            except Exception:
                failures += 1
                continue
            if hasattr(repo, "update_job_progress") and total_eligible:
                await repo.update_job_progress(job["id"], min(99, int(index * 100 / total_eligible)))
    return {"profile_id": str(profile_id), "post_id": str(post_id) if post_id else None,
            "attached": attached, "uploaded": uploaded, "reused": reused,
            "skipped": skipped, "failures": failures, "total_eligible": total_eligible,
            "completed": attached + failures}
