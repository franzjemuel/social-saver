"""Worker-only private persistence for Highlight Story media."""

from pathlib import Path

from core.config import settings
from core.profile_archive_media import _suffix_for_content_type
from core.storage import R2Storage
from providers.base import SourceUnavailable


class HighlightStoryMediaService:
    """Persist a verified temporary asset using the shared object store.

    Object dedupe deliberately matches profile archive behavior. If R2 upload
    succeeds but database recording fails, no worker deletes the deterministic
    key: a concurrent writer could have claimed it; reconciliation owns orphan
    cleanup.
    """

    def __init__(self, repo, storage=None):
        self.repo = repo
        self.storage = storage or R2Storage(
            settings.r2_account_id, settings.r2_access_key_id,
            settings.r2_secret_access_key, settings.r2_bucket,
            settings.r2_presign_seconds,
        )

    async def persist(self, *, user_id, profile_id, asset_id, downloaded):
        try:
            existing = await self.repo.get_stored_object_by_sha(downloaded.sha256)
        except Exception:
            raise SourceUnavailable("Highlight media storage lookup failed") from None
        reused = existing is not None
        if existing is not None:
            object_id = existing["id"]
        else:
            key = f"archive/{downloaded.sha256[:2]}/{downloaded.sha256}{_suffix_for_content_type(downloaded.content_type)}"
            try:
                await self.storage.put_file(Path(downloaded.path), key, downloaded.content_type)
            except Exception:
                raise SourceUnavailable("Highlight media storage upload failed") from None
            try:
                object_id = await self.repo.create_stored_object(
                    downloaded.sha256, key, downloaded.size_bytes, downloaded.content_type,
                )
            except Exception:
                raise SourceUnavailable("Highlight media storage record failed") from None
        try:
            attached = await self.repo.attach_owned_highlight_story_media_object(
                user_id, profile_id, asset_id, object_id,
            )
        except Exception:
            raise SourceUnavailable("Highlight media storage attachment failed") from None
        if not attached:
            raise SourceUnavailable("Highlight media storage attachment failed")
        return reused
