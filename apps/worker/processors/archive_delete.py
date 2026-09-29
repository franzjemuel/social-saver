from core.archive import ArchiveService


async def process_purge_archive(job, repo):
    """Delete only blobs that are still unreferenced when cleanup actually executes."""
    entry_id = job["input"]["archive_entry_id"]
    service = ArchiveService(repo)
    objects = await repo.list_unreferenced_objects_for_archive_entry(entry_id)
    deleted = 0
    for obj in objects:
        await service.storage.delete(obj["storage_key"])
        await repo.mark_stored_object_deleted(obj["id"])
        deleted += 1
    return {"archive_entry_id": str(entry_id), "objects_deleted": deleted}
