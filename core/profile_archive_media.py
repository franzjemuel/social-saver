from pathlib import Path

from core.config import settings
from core.storage import R2Storage


class ProfileArchiveMediaService:
    """Worker-only persistence for a mirrored profile asset.

    The object store is shared with the established archive flow; only the
    tenant-scoped association is profile-specific.
    """

    def __init__(self, repo, storage=None):
        self.repo = repo
        self.storage = storage or R2Storage(
            settings.r2_account_id, settings.r2_access_key_id,
            settings.r2_secret_access_key, settings.r2_bucket,
            settings.r2_presign_seconds,
        )

    async def persist(self, *, user_id, profile_id, asset_id, downloaded, suffix=".mp4"):
        existing = await self.repo.get_stored_object_by_sha(downloaded.sha256)
        reused = existing is not None
        if existing:
            object_id = existing["id"]
        else:
            key = f"archive/{downloaded.sha256[:2]}/{downloaded.sha256}{suffix}"
            await self.storage.put_file(Path(downloaded.path), key, downloaded.content_type)
            object_id = await self.repo.create_stored_object(
                downloaded.sha256, key, downloaded.size_bytes, downloaded.content_type,
            )
        attached = await self.repo.attach_owned_archived_post_media_object(
            user_id, profile_id, asset_id, object_id,
        )
        if not attached:
            raise PermissionError("Archived profile media asset not found")
        return reused
