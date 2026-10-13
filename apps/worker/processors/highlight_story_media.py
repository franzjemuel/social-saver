"""Synthetic-only, worker-owned archival of one Highlight Story's media."""

from pathlib import Path
from tempfile import TemporaryDirectory

from core.highlight_media import HighlightStoryMediaService
from providers.base import (
    HighlightStoryMediaDescriptor,
    HighlightStoryMediaProvider,
    SourceUnavailable,
)
from providers.tiktok.highlight_media_download import TikTokHighlightMediaDownloader


class DisabledHighlightStoryMediaProvider(HighlightStoryMediaProvider):
    """Fail closed: no live descriptor source is registered in this increment."""

    async def resolve_story_media(self, **_):
        raise SourceUnavailable("TikTok Highlight media acquisition is not enabled")


def _validate_descriptors(value):
    if not isinstance(value, tuple) or not value or len(value) > 20:
        raise SourceUnavailable("TikTok Highlight media descriptors are invalid")
    seen = set()
    for descriptor in value:
        if not isinstance(descriptor, HighlightStoryMediaDescriptor):
            raise SourceUnavailable("TikTok Highlight media descriptors are invalid")
        if (descriptor.position < 0 or descriptor.position > 9999
                or descriptor.asset_type not in {"video", "photo"}
                or not descriptor.candidates or len(descriptor.candidates) > 3
                or descriptor.position in seen):
            raise SourceUnavailable("TikTok Highlight media descriptors are invalid")
        seen.add(descriptor.position)
    return value


async def process_archive_highlight_story_media(job, repo, provider=None, downloader=None, media_service=None):
    """Persist available positions; descriptors remain in process memory only."""
    payload = job.get("input") or {}
    profile_id, membership_id = payload.get("profile_id"), payload.get("membership_id")
    if not isinstance(profile_id, str) or not isinstance(membership_id, str):
        raise SourceUnavailable("Highlight media request is invalid")
    target = await repo.get_owned_highlight_story_media_target(job["user_id"], profile_id, membership_id)
    if target is None:
        raise SourceUnavailable("Highlight media request is unavailable")
    provider = provider or DisabledHighlightStoryMediaProvider()
    try:
        descriptors = _validate_descriptors(await provider.resolve_story_media(
            username=target["username"], provider_namespace=target["provider_namespace"],
            provider_collection_id=target["provider_collection_id"], provider_item_id=target["provider_item_id"],
        ))
    except SourceUnavailable:
        raise
    except Exception:
        raise SourceUnavailable("TikTok Highlight media resolution failed") from None

    # Only durable, non-expiring shape enters the database. Candidates are not
    # put into a job checkpoint, error message, or repository call.
    seeded = await repo.upsert_owned_highlight_story_media_assets(
        job["user_id"], profile_id, membership_id, descriptors,
    )
    if seeded is None:
        raise SourceUnavailable("Highlight media request is unavailable")
    pending = await repo.list_owned_highlight_story_media_assets(job["user_id"], profile_id, membership_id)
    by_position = {item.position: item for item in descriptors}
    downloader = downloader or TikTokHighlightMediaDownloader()
    media_service = media_service or HighlightStoryMediaService(repo)
    attached = uploaded = reused = failures = 0
    with TemporaryDirectory(prefix=f"social-saver-highlight-{job['id']}-") as root:
        root_path = Path(root)
        for index, asset in enumerate(pending, start=1):
            descriptor = by_position.get(asset["position"])
            if descriptor is None or descriptor.asset_type != asset["asset_type"]:
                failures += 1
                await repo.record_owned_highlight_story_media_failure(
                    job["user_id"], profile_id, asset["id"], "descriptor_unavailable",
                )
                continue
            try:
                downloaded = await downloader.download(descriptor, root_path / f"{asset['id']}.asset")
                was_reused = await media_service.persist(
                    user_id=job["user_id"], profile_id=profile_id, asset_id=asset["id"], downloaded=downloaded,
                )
            except SourceUnavailable:
                failures += 1
                await repo.record_owned_highlight_story_media_failure(
                    job["user_id"], profile_id, asset["id"], "acquisition_or_storage_failed",
                )
                continue
            except Exception:
                failures += 1
                await repo.record_owned_highlight_story_media_failure(
                    job["user_id"], profile_id, asset["id"], "acquisition_or_storage_failed",
                )
                continue
            attached += 1
            reused += int(was_reused)
            uploaded += int(not was_reused)
            if hasattr(repo, "update_job_progress"):
                await repo.update_job_progress(
                    job["id"], min(99, int(index * 100 / max(1, len(pending)))),
                )
    return {
        "assets_discovered": len(descriptors), "assets_seeded": seeded["inserted"],
        "attached": attached, "uploaded": uploaded, "reused": reused,
        "failures": failures, "pending_after": max(0, len(pending) - attached),
    }
