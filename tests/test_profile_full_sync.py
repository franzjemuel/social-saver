from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from apps.worker.processors.profile_full_sync import (
    TikTokProfileFullSyncFailure,
    process_full_sync_profile,
)
from providers.base import SourceUnavailable
from providers.tiktok.scanner import TikTokProfileScan, TikTokScannedPost, TikTokScannedProfile

P = str(UUID("11111111-1111-1111-1111-111111111111"))


def scan_posts(count=109):
    return [
        TikTokScannedPost(
            str(index),
            f"https://example.invalid/{index}",
            index % 17 == 0,
            f"caption {index}",
        )
        for index in range(count)
    ]


class Scanner:
    def __init__(self, count=109, account_id="stable"):
        self.count = count
        self.account_id = account_id

    def scan(self, _):
        return TikTokProfileScan(
            TikTokScannedProfile("creator", self.account_id, "secret"),
            scan_posts(self.count),
        )


class Resolver:
    def __init__(self, failures=None):
        self.failures = set(failures or ())
        self.calls = []

    async def resolve(self, url):
        self.calls.append(url)
        post_id = url.rsplit("/", 1)[-1]
        if post_id in self.failures:
            raise SourceUnavailable("provider detail")
        return {
            "id": post_id,
            "webpage_url": url,
            "thumbnail": "https://example.invalid/poster.jpg",
            "description": f"rich {post_id}",
        }


class Repo:
    def __init__(self, *, inserted=None):
        self.inserted = set(inserted) if inserted is not None else None
        self.calls = []
        self.checkpoints = []

    async def get_owned_profile_sync_target(self, *_):
        return {"username": "creator", "platform_account_id": "stable"}

    async def update_job_progress(self, *args):
        self.calls.append(("progress", args))

    async def reconcile_archived_profile_presence(self, *args):
        self.calls.append(("reconcile", args))
        return (1, 1)

    async def upsert_archived_profile(self, *_):
        self.calls.append(("profile",))
        return P

    async def insert_archived_posts_if_missing(self, _user_id, _profile_id, posts):
        ids = {post.platform_post_id for post in posts}
        self.calls.append(("index", tuple(sorted(ids, key=int))))
        return ids if self.inserted is None else set(self.inserted)

    async def upsert_archived_post(self, _user_id, _profile_id, post):
        self.calls.append(("rich", post.platform_post_id))
        return P, False

    async def update_profile_full_sync_checkpoint(self, _job_id, checkpoint, progress):
        snapshot = dict(checkpoint)
        self.checkpoints.append((snapshot, progress))


@pytest.mark.asyncio
async def test_full_sync_indexes_and_enriches_all_109_posts():
    repo = Repo()
    resolver = Resolver()
    result = await process_full_sync_profile(
        {"id": "job", "user_id": "user", "input": {"profile_id": P}},
        repo,
        Scanner(),
        resolver,
    )
    assert len(next(call for call in repo.calls if call[0] == "index")[1]) == 109
    assert len(resolver.calls) == 109
    assert result == {
        "posts_discovered": 109,
        "posts_processed": 109,
        "posts_added": 109,
        "posts_refreshed": 0,
        "posts_removed": 1,
        "posts_restored": 1,
        "posts_enriched": 109,
        "metadata_failures": 0,
    }
    final_checkpoint, progress = repo.checkpoints[-1]
    assert len(final_checkpoint["processed_post_ids"]) == 109
    assert progress == 95


@pytest.mark.asyncio
async def test_full_sync_partial_metadata_failure_keeps_index_and_completes():
    repo = Repo()
    resolver = Resolver({"13"})
    result = await process_full_sync_profile(
        {"id": "job", "user_id": "user", "input": {"profile_id": P}},
        repo,
        Scanner(),
        resolver,
    )
    assert result["posts_added"] == 109
    assert result["posts_enriched"] == 108
    assert result["metadata_failures"] == 1
    assert len(repo.checkpoints[-1][0]["processed_post_ids"]) == 109


@pytest.mark.asyncio
async def test_full_sync_resumes_from_private_checkpoint():
    processed = [str(index) for index in range(50)]
    checkpoint = {
        "version": 1,
        "processed_post_ids": processed,
        "new_post_ids": [str(index) for index in range(109)],
        "posts_discovered": 109,
        "posts_added": 109,
        "posts_refreshed": 0,
        "posts_removed": 1,
        "posts_restored": 0,
        "posts_enriched": 50,
        "metadata_failures": 0,
    }
    repo = Repo(inserted=set())
    resolver = Resolver()
    result = await process_full_sync_profile(
        {
            "id": "job",
            "user_id": "user",
            "input": {"profile_id": P, "checkpoint": checkpoint},
        },
        repo,
        Scanner(),
        resolver,
    )
    assert len(resolver.calls) == 59
    assert result["posts_processed"] == 109
    assert result["posts_added"] == 109
    assert result["posts_enriched"] == 109
    assert result["posts_removed"] == 2
    assert result["posts_restored"] == 1


@pytest.mark.asyncio
async def test_full_sync_identity_mismatch_mutates_nothing():
    repo = Repo()
    with pytest.raises(TikTokProfileFullSyncFailure) as exc:
        await process_full_sync_profile(
            {"id": "job", "user_id": "user", "input": {"profile_id": P}},
            repo,
            Scanner(account_id="different"),
            Resolver(),
        )
    assert exc.value.code == "profile_changed"
    assert repo.calls == []
    assert repo.checkpoints == []


@pytest.mark.asyncio
async def test_full_sync_api_hides_foreign_profile(monkeypatch):
    class R:
        async def create_owned_profile_full_sync_job(self, *_, **__):
            return None

    monkeypatch.setattr(api, "_repo", R())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="q"))
    with pytest.raises(HTTPException) as exc:
        await api.full_sync_profile_archive(
            P, identity={"app_user_id": "user", "telegram_user_id": 1},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_full_sync_status_projects_counts_not_checkpoint_ids(monkeypatch):
    class R:
        async def get_owned_profile_full_sync(self, *_):
            return {
                "id": UUID(P),
                "status": "running",
                "progress": 61,
                "result": None,
                "error_code": None,
                "checkpoint": {
                    "processed_post_ids": ["secret-a", "secret-b"],
                    "new_post_ids": ["secret-a"],
                    "posts_discovered": 109,
                    "posts_added": 97,
                    "posts_refreshed": 12,
                    "posts_removed": 1,
                    "posts_restored": 0,
                    "posts_enriched": 100,
                    "metadata_failures": 2,
                },
            }

    monkeypatch.setattr(api, "_repo", R())
    result = await api.profile_full_sync_status(
        P, identity={"app_user_id": "user", "telegram_user_id": 1},
    )
    dumped = result.model_dump()
    assert dumped["summary"]["posts_processed"] == 2
    assert dumped["summary"]["posts_discovered"] == 109
    assert "processed_post_ids" not in dumped["summary"]
    assert "new_post_ids" not in dumped["summary"]
