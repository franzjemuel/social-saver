"""Worker-owned full TikTok profile indexing with resumable rich enrichment."""
import asyncio

from providers.base import ArchivedProfile, SourceUnavailable
from providers.tiktok.metadata import TikTokPostMetadataResolver
from providers.tiktok.normalize import normalize_scanned_tiktok_post, normalize_tiktok_post
from providers.tiktok.scanner import TikTokProfileScanner


class TikTokProfileFullSyncFailure(Exception):
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


def _counter(checkpoint, name):
    value = checkpoint.get(name, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _checkpoint_payload(
    *, processed, new_ids, posts_discovered, posts_added, posts_refreshed,
    posts_removed, posts_restored, posts_enriched, metadata_failures,
):
    return {
        "version": 1,
        "processed_post_ids": sorted(processed),
        "new_post_ids": sorted(new_ids),
        "posts_discovered": posts_discovered,
        "posts_added": posts_added,
        "posts_refreshed": posts_refreshed,
        "posts_removed": posts_removed,
        "posts_restored": posts_restored,
        "posts_enriched": posts_enriched,
        "metadata_failures": metadata_failures,
    }


async def process_full_sync_profile(job, repo, scanner=None, resolver=None):
    payload = job["input"] or {}
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str):
        raise TikTokProfileFullSyncFailure("full_sync_failed")

    target = await repo.get_owned_profile_sync_target(job["user_id"], profile_id)
    if target is None:
        raise TikTokProfileFullSyncFailure("full_sync_failed")

    scanner = scanner or TikTokProfileScanner()
    resolver = resolver or TikTokPostMetadataResolver()
    try:
        scan = await asyncio.to_thread(scanner.scan, target["username"])
    except Exception as exc:
        raise TikTokProfileFullSyncFailure("temporarily_unavailable") from exc

    if scan.profile.user_id != target["platform_account_id"]:
        raise TikTokProfileFullSyncFailure("profile_changed")

    prior = payload.get("checkpoint")
    checkpoint = prior if isinstance(prior, dict) else {}
    current_ids = [post.post_id for post in scan.posts]
    current_id_set = set(current_ids)
    processed = {
        value for value in checkpoint.get("processed_post_ids", [])
        if isinstance(value, str) and value in current_id_set
    }
    new_ids = {
        value for value in checkpoint.get("new_post_ids", [])
        if isinstance(value, str)
    }
    posts_added = _counter(checkpoint, "posts_added")
    posts_refreshed = _counter(checkpoint, "posts_refreshed")
    posts_removed = _counter(checkpoint, "posts_removed")
    posts_restored = _counter(checkpoint, "posts_restored")
    posts_enriched = _counter(checkpoint, "posts_enriched")
    metadata_failures = _counter(checkpoint, "metadata_failures")

    await repo.update_job_progress(job["id"], 10)
    removed, restored = await repo.reconcile_archived_profile_presence(
        job["user_id"], profile_id, current_ids,
    )
    posts_removed += removed
    posts_restored += restored

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
    new_ids.update(inserted)
    posts_added += len(inserted)

    checkpoint = _checkpoint_payload(
        processed=processed,
        new_ids=new_ids,
        posts_discovered=len(scan.posts),
        posts_added=posts_added,
        posts_refreshed=posts_refreshed,
        posts_removed=posts_removed,
        posts_restored=posts_restored,
        posts_enriched=posts_enriched,
        metadata_failures=metadata_failures,
    )
    await repo.update_profile_full_sync_checkpoint(job["id"], checkpoint, 20)

    total = len(scan.posts)
    for scanned in scan.posts:
        if scanned.post_id in processed:
            continue
        try:
            metadata = await resolver.resolve(scanned.canonical_url)
            post = normalize_tiktok_post(
                metadata,
                is_photo=scanned.is_photo,
                canonical_url=scanned.canonical_url,
                photo_candidate_groups=scanned.image_url_candidates,
            )
        except SourceUnavailable:
            metadata_failures += 1
        else:
            _, created = await repo.upsert_archived_post(job["user_id"], profile_id, post)
            posts_enriched += 1
            if created:
                new_ids.add(scanned.post_id)
                posts_added += 1
            elif scanned.post_id not in new_ids:
                posts_refreshed += 1

        processed.add(scanned.post_id)
        progress = 95 if total == 0 else min(95, 20 + int(75 * len(processed) / total))
        checkpoint = _checkpoint_payload(
            processed=processed,
            new_ids=new_ids,
            posts_discovered=total,
            posts_added=posts_added,
            posts_refreshed=posts_refreshed,
            posts_removed=posts_removed,
            posts_restored=posts_restored,
            posts_enriched=posts_enriched,
            metadata_failures=metadata_failures,
        )
        await repo.update_profile_full_sync_checkpoint(job["id"], checkpoint, progress)

    return {
        "posts_discovered": total,
        "posts_processed": len(processed),
        "posts_added": posts_added,
        "posts_refreshed": posts_refreshed,
        "posts_removed": posts_removed,
        "posts_restored": posts_restored,
        "posts_enriched": posts_enriched,
        "metadata_failures": metadata_failures,
    }
