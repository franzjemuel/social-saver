from pathlib import Path
from tempfile import TemporaryDirectory

from core.profile_archive_media import ProfileArchiveMediaService
from providers.base import SourceUnavailable, TerminalProviderError
from providers.tiktok.download import TikTokMediaDownloader
from providers.tiktok.image_download import TikTokImageDownloader


async def process_archive_profile_media(job, repo, downloader=None, media_service=None, image_downloader=None):
    """Persist owned TikTok video and ordered photo assets, one asset at a time."""
    profile_id = (job["input"] or {}).get("profile_id")
    post_id = (job["input"] or {}).get("post_id")
    if not profile_id:
        raise ValueError("Missing archived profile id")
    assets = await repo.list_owned_archived_media_assets(job["user_id"], profile_id, post_id)
    downloader = downloader or TikTokMediaDownloader()
    image_downloader = image_downloader or TikTokImageDownloader()
    attached = uploaded = reused = skipped = failures = 0
    last_failure = None
    total_eligible = len(assets)
    if not assets:
        return {"profile_id": str(profile_id), "post_id": str(post_id) if post_id else None,
                "attached": 0, "uploaded": 0, "reused": 0, "skipped": 1 if post_id else 0,
                "failures": 0, "total_eligible": 0, "completed": 0}
    media_service = media_service or ProfileArchiveMediaService(repo)
    with TemporaryDirectory(prefix=f"social-saver-profile-{job['id']}-") as tmp:
        root = Path(tmp)
        for index, asset in enumerate(assets, start=1):
            if asset["asset_type"] not in {"video", "photo"}:
                skipped += 1
                continue
            try:
                if asset["asset_type"] == "video":
                    downloaded = await downloader.download_post(
                        asset["original_url"], root / f"{asset['id']}.mp4",
                    )
                else:
                    downloaded = await image_downloader.download_asset(
                        _image_candidates(asset), root / f"{asset['id']}.image",
                    )
            except (TerminalProviderError, SourceUnavailable) as exc:
                if post_id and asset["asset_type"] == "video":
                    raise
                # A concrete post may vanish or become temporarily unavailable.
                # Keep attachments from other posts; job results expose only the
                # aggregate count, never provider output or URLs.
                failures += 1
                last_failure = exc
                continue
            except Exception:
                # Provider exceptions can include ephemeral source URLs.  The
                # worker may report this exception to telemetry, so retain only
                # the stage/category rather than a chained provider error.
                if post_id and asset["asset_type"] == "video":
                    raise SourceUnavailable("TikTok media download failed") from None
                failures += 1
                last_failure = SourceUnavailable("TikTok image download failed") if asset["asset_type"] == "photo" else SourceUnavailable("TikTok media download failed")
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
            except SourceUnavailable as exc:
                if post_id and asset["asset_type"] == "video":
                    raise
                failures += 1
                last_failure = exc
                continue
            except Exception:
                if post_id and asset["asset_type"] == "video":
                    raise SourceUnavailable("profile media storage failed") from None
                failures += 1
                last_failure = SourceUnavailable("profile media storage failed")
                continue
            if hasattr(repo, "update_job_progress") and total_eligible:
                await repo.update_job_progress(job["id"], min(99, int(index * 100 / total_eligible)))
    # A carousel can persist useful earlier/later positions despite a failed
    # candidate. The missing positions remain unarchived and are selected again
    # on an explicit retry; the safe aggregate result never claims completeness.
    if post_id and failures:
        raise last_failure or SourceUnavailable("TikTok image download failed")
    return {"profile_id": str(profile_id), "post_id": str(post_id) if post_id else None,
            "attached": attached, "uploaded": uploaded, "reused": reused,
            "skipped": skipped, "failures": failures, "total_eligible": total_eligible,
            "completed": attached + failures}


def _image_candidates(asset):
    """Read controlled scanner hints without letting malformed metadata escape."""
    candidates = []
    source = asset.get("source_url") if hasattr(asset, "get") else asset["source_url"]
    if isinstance(source, str):
        candidates.append(source)
    metadata = asset.get("metadata") if hasattr(asset, "get") else asset["metadata"]
    fallbacks = metadata.get("fallback_source_urls") if isinstance(metadata, dict) else ()
    if isinstance(fallbacks, list):
        candidates.extend(value for value in fallbacks if isinstance(value, str))
    return tuple(dict.fromkeys(candidates))
