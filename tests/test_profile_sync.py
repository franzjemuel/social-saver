from types import SimpleNamespace
from uuid import UUID

import pytest

import apps.api.main as api
from apps.worker.processors.profile_sync import process_sync_profile, TikTokProfileSyncFailure
from providers.tiktok.scanner import TikTokProfileScan, TikTokScannedProfile, TikTokScannedPost

P = str(UUID("11111111-1111-1111-1111-111111111111"))


class Repo:
    def __init__(self):
        self.calls = []

    async def get_owned_profile_sync_target(self, *_):
        return {"username": "creator", "platform_account_id": "stable"}

    async def update_job_progress(self, *args):
        self.calls.append(("progress", args))

    async def reconcile_archived_profile_presence(self, *args):
        self.calls.append(("reconcile", args))
        return (1, 1)

    async def upsert_archived_profile(self, *_):
        return P

    async def insert_archived_posts_if_missing(self, _user_id, _profile_id, posts):
        self.calls.append(("index", tuple(post.platform_post_id for post in posts)))
        return {post.platform_post_id for post in posts}

    async def upsert_archived_post(self, *args):
        self.calls.append(("post", args))
        return P, False


class Scanner:
    def scan(self, _):
        return TikTokProfileScan(
            TikTokScannedProfile("creator", "stable", "secret"),
            [
                TikTokScannedPost("recent", "https://example.invalid/recent", False),
                TikTokScannedPost("old", "https://example.invalid/old", False),
            ],
        )


class Resolver:
    async def resolve(self, url):
        if url.endswith("old"):
            raise __import__("providers.base", fromlist=["SourceUnavailable"]).SourceUnavailable("x")
        return {
            "id": url.rsplit("/", 1)[-1],
            "webpage_url": url,
            "thumbnail": "https://example.invalid/a.jpg",
            "description": "x",
        }


@pytest.mark.asyncio
async def test_sync_uses_full_scan_for_reconciliation_and_indexes_all_ids():
    repo = Repo()
    result = await process_sync_profile(
        {"id": "j", "user_id": "u", "input": {"profile_id": P}},
        repo,
        Scanner(),
        Resolver(),
    )
    assert repo.calls[1][0] == "reconcile"
    assert repo.calls[1][1][2] == ["recent", "old"]
    assert next(call for call in repo.calls if call[0] == "index")[1] == ("recent", "old")
    assert result == {
        "posts_added": 2,
        "posts_refreshed": 0,
        "posts_removed": 1,
        "posts_restored": 1,
        "metadata_failures": 1,
    }


@pytest.mark.asyncio
async def test_normal_sync_indexes_all_but_richly_enriches_only_twelve():
    posts = [
        TikTokScannedPost(str(i), f"https://example.invalid/{i}", False)
        for i in range(20)
    ]

    class ManyScanner:
        def scan(self, _):
            return TikTokProfileScan(
                TikTokScannedProfile("creator", "stable", "secret"),
                posts,
            )

    class CountingResolver(Resolver):
        def __init__(self):
            self.calls = []

        async def resolve(self, url):
            self.calls.append(url)
            return await super().resolve(url)

    resolver = CountingResolver()
    repo = Repo()
    result = await process_sync_profile(
        {"id": "j", "user_id": "u", "input": {"profile_id": P}},
        repo,
        ManyScanner(),
        resolver,
    )
    assert len(next(call for call in repo.calls if call[0] == "index")[1]) == 20
    assert len(resolver.calls) == 12
    assert result["posts_added"] == 20
    assert result["posts_refreshed"] == 0


@pytest.mark.asyncio
async def test_sync_identity_mismatch_mutates_nothing():
    class Bad(Scanner):
        def scan(self, _):
            return TikTokProfileScan(TikTokScannedProfile("creator", "other", "secret"), [])

    repo = Repo()
    with pytest.raises(TikTokProfileSyncFailure) as exc:
        await process_sync_profile(
            {"id": "j", "user_id": "u", "input": {"profile_id": P}},
            repo,
            Bad(),
            Resolver(),
        )
    assert exc.value.code == "profile_changed"
    assert repo.calls == []


@pytest.mark.asyncio
async def test_sync_api_hides_foreign_profiles(monkeypatch):
    class R:
        async def create_owned_profile_sync_job(self, *_, **__):
            return None

    monkeypatch.setattr(api, "_repo", R())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="q"))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await api.sync_profile_archive(
            P, identity={"app_user_id": "u", "telegram_user_id": 1},
        )
    assert exc.value.status_code == 404
