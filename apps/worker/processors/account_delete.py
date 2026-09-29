from core.archive import ArchiveService


async def process_purge_account(job, repo):
    """Physically purge orphaned blobs, then remove the tenant root.

    Shared/deduplicated objects survive whenever another live archive references
    them. Deleting app_users last lets database cascades remove watches, jobs,
    subscriptions, Telegram identity and remaining tenant-owned rows only after
    storage cleanup has succeeded.
    """
    user_id = job["user_id"]
    started = await repo.begin_account_purge(user_id)
    if not started:
        return {"account_deleted": True, "objects_deleted": 0, "already_gone": True}

    service = ArchiveService(repo)
    objects = await repo.list_unreferenced_objects_for_user(user_id)
    deleted = 0
    for obj in objects:
        await service.storage.delete(obj["storage_key"])
        await repo.mark_stored_object_deleted(obj["id"])
        deleted += 1

    await repo.finalize_account_deletion(user_id)
    return {"account_deleted": True, "objects_deleted": deleted}
