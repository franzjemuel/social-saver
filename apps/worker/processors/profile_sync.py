"""Worker-owned, bounded TikTok profile reconciliation."""
import asyncio

from providers.base import ArchivedProfile, SourceUnavailable
from providers.tiktok.constants import INITIAL_PROFILE_IMPORT_POST_LIMIT
from providers.tiktok.metadata import TikTokPostMetadataResolver
from providers.tiktok.normalize import normalize_scanned_tiktok_post, normalize_tiktok_post
from providers.tiktok.scanner import TikTokProfileScanner


class TikTokProfileSyncFailure(Exception):
    def __init__(self, code):
        self.code = code


def _scanner_stub(scanned):
    return normalize_scanned_tiktok_post(
        scanned.post_id,
        scanned.canonical_url,
        is_photo=scanned.is_photo,
        caption=scanned.description,
        photo_candidate_groups=scanned.image_url_candidates,
    )


async def process_sync_profile(job, repo, scanner=None, resolver=None):
    payload = job["input"] or {}
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str):
        raise TikTokProfileSyncFailure("sync_failed")
    target = await repo.get_owned_profile_sync_target(job["user_id"], profile_id)
    if target is None:
        raise TikTokProfileSyncFailure("sync_failed")
    scanner = scanner or TikTokProfileScanner()
    resolver = resolver or TikTokPostMetadataResolver()
    try:
        scan = await asyncio.to_thread(scanner.scan, target["username"])
    except Exception as exc:
        raise TikTokProfileSyncFailure("temporarily_unavailable") from exc
    if scan.profile.user_id != target["platform_account_id"]:
        raise TikTokProfileSyncFailure("profile_changed")

    await repo.update_job_progress(job["id"], 25)
    current_ids = [post.post_id for post in scan.posts]
    removed, restored = await repo.reconcile_archived_profile_presence(
        job["user_id"], profile_id, current_ids,
    )
    await repo.upsert_archived_profile(
        job["user_id"],
        ArchivedProfile(
            platform="tiktok",
            platform_account_id=scan.profile.user_id,
            username=scan.profile.username,
            display_name=scan.profile.display_name,
            avatar_url=scan.profile.avatar_url,
            metadata={"sec_uid": scan.profile.sec_uid, "scanner": "tt-dlp"},
        ),
    )

    inserted = await repo.insert_archived_posts_if_missing(
        job["user_id"], profile_id, [_scanner_stub(post) for post in scan.posts],
    )
    added = len(inserted)
    refreshed = failures = 0

    await repo.update_job_progress(job["id"], 45)
    for scanned in scan.posts[:INITIAL_PROFILE_IMPORT_POST_LIMIT]:
        try:
            metadata = await resolver.resolve(scanned.canonical_url)
            post = normalize_tiktok_post(
                metadata,
                is_photo=scanned.is_photo,
                canonical_url=scanned.canonical_url,
                photo_candidate_groups=scanned.image_url_candidates,
            )
        except SourceUnavailable:
            failures += 1
            continue
        _, created = await repo.upsert_archived_post(job["user_id"], profile_id, post)
        if created:
            inserted.add(scanned.post_id)
            added += 1
        elif scanned.post_id not in inserted:
            refreshed += 1

    await repo.update_job_progress(job["id"], 90)
    return {
        "posts_added": added,
        "posts_refreshed": refreshed,
        "posts_removed": removed,
        "posts_restored": restored,
        "metadata_failures": failures,
    }
