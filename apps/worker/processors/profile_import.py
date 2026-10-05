"""Worker-owned bounded metadata import after a confirmed profile preview."""

from providers.tiktok.constants import INITIAL_PROFILE_IMPORT_POST_LIMIT
from providers.tiktok.importer import TikTokProfileChanged, TikTokProfileImporter


class TikTokProfileImportFailure(Exception):
    """Browser-safe import failure category; never include provider detail."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def process_import_profile(job, repo, importer=None):
    payload = job["input"] or {}
    target = payload.get("target")
    expected = payload.get("expected_platform_account_id")
    if payload.get("platform") != "tiktok" or not isinstance(target, str) or not isinstance(expected, str):
        raise TikTokProfileImportFailure("import_failed")
    # This is deliberately backend-owned even if a malformed stored job exists.
    limit = INITIAL_PROFILE_IMPORT_POST_LIMIT
    importer = importer or TikTokProfileImporter(repo)
    try:
        await repo.update_job_progress(job["id"], 25)
        summary = await importer.import_profile(
            job["user_id"], target, expected_platform_account_id=expected, limit=limit,
        )
    except TikTokProfileChanged as exc:
        raise TikTokProfileImportFailure("profile_changed") from exc
    except Exception as exc:
        raise TikTokProfileImportFailure("temporarily_unavailable") from exc
    return {
        "profile_id": str(summary.archived_profile_id),
        "posts_imported": summary.posts_imported,
    }
