from dataclasses import dataclass
from core.config import settings
from core.storage import R2Storage

@dataclass(frozen=True)
class ArchiveResult:
    archive_entry_id: str
    stored_object_id: str
    storage_key: str
    reused_object: bool

class ArchiveService:
    """User ownership is separate from physical R2 objects.

    Multiple users may reference one public-media blob, but each user gets an
    independent archive entry. Deleting one user's entry cannot delete bytes
    still referenced by somebody else.
    """
    def __init__(self, repo):
        self.repo = repo
        self.storage = R2Storage(
            settings.r2_account_id,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
            settings.r2_bucket,
            settings.r2_presign_seconds,
        )

    async def archive_download(self, *, user_id, media_item_id, media_asset_id,
                               downloaded, platform, media_id, suffix):
        existing = await self.repo.get_stored_object_by_sha(downloaded.sha256)
        reused = existing is not None
        if existing:
            object_id, key = existing['id'], existing['storage_key']
        else:
            key = f"archive/{downloaded.sha256[:2]}/{downloaded.sha256}{suffix}"
            await self.storage.put_file(downloaded.path, key, downloaded.content_type)
            object_id = await self.repo.create_stored_object(
                downloaded.sha256, key, downloaded.size_bytes, downloaded.content_type
            )

        entry_id = await self.repo.get_or_create_archive_entry(user_id, media_item_id)
        await self.repo.attach_archive_asset(entry_id, media_asset_id, object_id)
        return ArchiveResult(str(entry_id), str(object_id), key, reused)

    async def download_url(self, user_id, archive_entry_id, media_asset_id):
        row = await self.repo.get_owned_archive_object(user_id, archive_entry_id, media_asset_id)
        if not row:
            raise PermissionError('Archive item not found')
        return await self.storage.presigned_get(row['storage_key'], settings.archive_presign_seconds)

    async def delete_entry(self, user_id, archive_entry_id):
        orphaned = await self.repo.soft_delete_archive_entry(user_id, archive_entry_id)
        if orphaned is None:
            raise PermissionError('Archive item not found')
        for obj in orphaned:
            await self.storage.delete(obj['storage_key'])
            await self.repo.mark_stored_object_deleted(obj['id'])
        return len(orphaned)
