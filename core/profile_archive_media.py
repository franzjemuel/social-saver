from pathlib import Path

from core.config import settings
from core.storage import R2Storage
from providers.base import SourceUnavailable


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

    async def persist(self, *, user_id, profile_id, asset_id, downloaded, suffix=None):
        suffix = suffix or _suffix_for_content_type(downloaded.content_type)
        try:
            existing = await self.repo.get_stored_object_by_sha(downloaded.sha256)
        except Exception:
            raise SourceUnavailable("profile media storage lookup failed") from None
        reused = existing is not None
        if existing:
            object_id = existing["id"]
        else:
            key = f"archive/{downloaded.sha256[:2]}/{downloaded.sha256}{suffix}"
            try:
                await self.storage.put_file(Path(downloaded.path), key, downloaded.content_type)
            except Exception:
                raise SourceUnavailable("profile media storage upload failed") from None
            try:
                object_id = await self.repo.create_stored_object(
                    downloaded.sha256, key, downloaded.size_bytes, downloaded.content_type,
                )
            except Exception:
                # Do not delete here. A separate concurrent writer can claim
                # this deterministic, content-addressed object between upload
                # and a non-atomic lookup. Retrying this SHA safely rewrites
                # the same private key; durable orphan cleanup belongs to a
                # reconciler that can prove an object has no references.
                raise SourceUnavailable("profile media storage record failed") from None
        try:
            attached = await self.repo.attach_owned_archived_post_media_object(
                user_id, profile_id, asset_id, object_id,
            )
        except Exception:
            raise SourceUnavailable("profile media storage attachment failed") from None
        if not attached:
            raise SourceUnavailable("profile media storage attachment failed")
        return reused


def _suffix_for_content_type(content_type):
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".mp4")
