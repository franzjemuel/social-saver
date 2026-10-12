from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from apps.worker.processors.profile_highlight_sync import (
    TikTokProfileHighlightSyncFailure,
    process_sync_profile_highlights,
)
from providers.base import (
    ArchivedHighlightCollectionSnapshot,
    ArchivedHighlightStoryMembership,
    HighlightDiscoveryResult,
)


PROFILE = str(UUID("aaaaaaa1-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))


def membership(identifier: str, position: int, media_type="video"):
    return ArchivedHighlightStoryMembership(
        provider_namespace="synthetic:tiktok-highlights",
        provider_item_id=identifier,
        provider_position=position,
        media_type=media_type,
        caption="safe metadata",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        duration_seconds=4.0,
        has_media_descriptor=True,
        has_cover_descriptor=True,
    )


def snapshot(identifier: str, memberships=(), *, complete=True):
    return ArchivedHighlightCollectionSnapshot(
        provider_namespace="synthetic:tiktok-highlights",
        provider_collection_id=identifier,
        title="Synthetic collection",
        reported_item_count=len(memberships),
        has_cover_descriptor=True,
        observation_complete=complete,
        memberships=tuple(memberships),
        upstream_completeness_verified=True,
    )


@dataclass
class Reconciliation:
    collection_created: bool = True
    memberships_created: int = 0
    memberships_refreshed: int = 0


class Repo:
    def __init__(self, *, claimed=True):
        self.claimed = claimed
        self.calls = []

    async def get_owned_profile_highlight_sync_target(self, *_):
        return {"username": "creator", "platform_account_id": "not-exposed"}

    async def claim_profile_highlight_sync_execution(self, *args):
        self.calls.append(("claim", args))
        return self.claimed

    async def update_job_progress(self, *args):
        self.calls.append(("progress", args))

    async def reconcile_owned_highlight_collection_snapshot(self, *_args):
        self.calls.append(("reconcile", _args))
        item_count = len(_args[-1].memberships)
        return Reconciliation(memberships_created=item_count)


class Provider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def discover_highlights(self, _username):
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_synthetic_highlight_sync_reconciles_validated_collections_only():
    repo = Repo()
    provider = Provider(HighlightDiscoveryResult((
        snapshot("one", (membership("a", 0), membership("b", 1))),
        snapshot("two", (membership("same-story", 0),), complete=False),
    ), incomplete_collections=1))
    result = await process_sync_profile_highlights(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE}}, repo, provider,
    )
    assert provider.calls == 1
    assert result == {
        "collections_observed": 2, "collections_created": 2, "collections_refreshed": 0,
        "collections_incomplete": 1, "memberships_created": 3, "memberships_refreshed": 0,
    }
    persisted = [call[-1] for name, call in repo.calls if name == "reconcile"]
    assert [item.provider_position for item in persisted[0].memberships] == [0, 1]
    # The worker never elevates unverified fixture/provider data to a claim of
    # independently verified upstream completeness.
    assert all(not value.upstream_completeness_verified for value in persisted)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    HighlightDiscoveryResult((snapshot("duplicate"), snapshot("duplicate"))),
    HighlightDiscoveryResult((snapshot("unsupported", (membership("a", 0, "audio"),)),)),
    HighlightDiscoveryResult((snapshot("bad", (membership("a", 0), membership("b", 0))),)),
])
async def test_invalid_discovery_never_mutates_any_collection(result):
    repo = Repo()
    provider = Provider(result)
    with pytest.raises(TikTokProfileHighlightSyncFailure) as exc:
        await process_sync_profile_highlights(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE}}, repo, provider,
        )
    assert exc.value.code == "highlights_invalid_response"
    assert not [entry for entry in repo.calls if entry[0] == "reconcile"]


@pytest.mark.asyncio
async def test_redelivery_claim_never_repeats_provider_execution():
    repo = Repo(claimed=False)
    provider = Provider(HighlightDiscoveryResult(()))
    with pytest.raises(TikTokProfileHighlightSyncFailure) as exc:
        await process_sync_profile_highlights(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE}}, repo, provider,
        )
    assert exc.value.code == "highlights_execution_indeterminate"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_disabled_provider_has_no_live_or_fixture_fallback():
    repo = Repo()
    with pytest.raises(TikTokProfileHighlightSyncFailure) as exc:
        await process_sync_profile_highlights(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": PROFILE}}, repo,
        )
    assert exc.value.code == "highlights_provider_disabled"
    assert not [entry for entry in repo.calls if entry[0] == "reconcile"]


@pytest.mark.asyncio
async def test_disabled_api_gate_does_not_enqueue_or_touch_repository(monkeypatch):
    class NoQueueRepo:
        async def create_owned_profile_highlight_sync_job(self, *_args, **_kwargs):
            raise AssertionError("disabled gate must not enqueue")

    monkeypatch.setattr(api, "_repo", NoQueueRepo())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="queue"))
    monkeypatch.setattr(api.settings, "tiktok_highlights_sync_enabled", False)
    with pytest.raises(HTTPException) as exc:
        await api.sync_profile_highlights(PROFILE, identity={"app_user_id": "tenant", "telegram_user_id": 1})
    assert exc.value.status_code == 404
    assert exc.value.detail == "profile_highlights_sync_not_found"


@pytest.mark.asyncio
async def test_enabled_api_hides_foreign_profile_and_status(monkeypatch):
    class ForeignRepo:
        async def create_owned_profile_highlight_sync_job(self, *_args, **_kwargs):
            return None

        async def get_owned_profile_highlight_sync(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(api, "_repo", ForeignRepo())
    monkeypatch.setattr(api, "_queue", SimpleNamespace(queue_name="queue"))
    monkeypatch.setattr(api.settings, "tiktok_highlights_sync_enabled", True)
    identity = {"app_user_id": "tenant", "telegram_user_id": 1}
    with pytest.raises(HTTPException) as create_error:
        await api.sync_profile_highlights(PROFILE, identity=identity)
    with pytest.raises(HTTPException) as status_error:
        await api.profile_highlight_sync_status(PROFILE, identity=identity)
    assert (create_error.value.status_code, create_error.value.detail) == (404, "profile_highlights_sync_not_found")
    assert (status_error.value.status_code, status_error.value.detail) == (404, "profile_highlights_sync_not_found")
