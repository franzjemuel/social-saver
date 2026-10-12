"""Tenant-owned, metadata-only TikTok Highlight synchronization.

No production provider is registered here.  Synthetic providers are injected
by tests only; enabling a live provider, budgets, and durable per-call receipts
requires its own reviewed increment.
"""
from __future__ import annotations

from dataclasses import replace
import asyncio

from core.config import settings
from core.repository import HighlightSnapshotValidationError, validate_highlight_snapshot
from providers.base import HighlightDiscoveryResult, ProfileHighlightsDiscoveryProvider
from providers.tiktok.scanner import TikTokProfileScanner


MAX_HIGHLIGHT_COLLECTIONS_PER_SYNC = 100
MAX_HIGHLIGHT_MEMBERSHIPS_PER_SYNC = 1_000
SUPPORTED_HIGHLIGHT_MEDIA_TYPES = frozenset({"video", "photo"})


class TikTokProfileHighlightSyncFailure(Exception):
    def __init__(self, code: str):
        self.code = code


class DisabledProfileHighlightsProvider(ProfileHighlightsDiscoveryProvider):
    """Fail closed: a hosted worker never has a fixture or live fallback."""

    async def discover_highlights(self, username: str) -> HighlightDiscoveryResult:
        raise TikTokProfileHighlightSyncFailure("highlights_provider_disabled")


def _discovery_outcome(
    snapshot_count: int, incomplete_collections: int, failed_collections: int,
) -> str:
    """Project provider aggregate state even when it returned no snapshots."""
    if failed_collections:
        return "partial_failure" if snapshot_count else "failed"
    if incomplete_collections:
        return "incomplete"
    return "complete"


def _validate_discovery_result(result: object) -> tuple:
    """Validate the whole response before any collection can be persisted."""
    if not isinstance(result, HighlightDiscoveryResult):
        raise TikTokProfileHighlightSyncFailure("highlights_invalid_response")
    if (
        isinstance(result.incomplete_collections, bool)
        or not isinstance(result.incomplete_collections, int)
        or result.incomplete_collections < 0
        or isinstance(result.failed_collections, bool)
        or not isinstance(result.failed_collections, int)
        or result.failed_collections < 0
        or len(result.snapshots) > MAX_HIGHLIGHT_COLLECTIONS_PER_SYNC
    ):
        raise TikTokProfileHighlightSyncFailure("highlights_invalid_response")

    identities = set()
    membership_count = 0
    sanitized = []
    for snapshot in result.snapshots:
        try:
            validate_highlight_snapshot(snapshot)
        except HighlightSnapshotValidationError as exc:
            raise TikTokProfileHighlightSyncFailure("highlights_invalid_response") from exc
        identity = (snapshot.provider_namespace, snapshot.provider_collection_id)
        if identity in identities:
            raise TikTokProfileHighlightSyncFailure("highlights_invalid_response")
        identities.add(identity)
        membership_count += len(snapshot.memberships)
        if membership_count > MAX_HIGHLIGHT_MEMBERSHIPS_PER_SYNC:
            raise TikTokProfileHighlightSyncFailure("highlights_invalid_response")
        if any(member.media_type not in SUPPORTED_HIGHLIGHT_MEDIA_TYPES for member in snapshot.memberships):
            raise TikTokProfileHighlightSyncFailure("highlights_invalid_response")
        # A future independently reviewed provider can establish this proof.
        # Synthetic metadata does not claim a provider response is upstream-complete.
        sanitized.append(replace(snapshot, upstream_completeness_verified=False))
    return tuple(sanitized)


async def process_sync_profile_highlights(
    job, repo, provider: ProfileHighlightsDiscoveryProvider | None = None, scanner=None,
    execution_enabled: bool | None = None,
):
    # A previously queued job must still fail closed while the server-owned
    # feature flag is off; do this before scanner/provider network activity or
    # consuming the durable execution claim.
    if execution_enabled is None:
        execution_enabled = settings.tiktok_highlights_sync_enabled
    if not execution_enabled:
        raise TikTokProfileHighlightSyncFailure("highlights_feature_disabled")
    payload = job.get("input") or {}
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str):
        raise TikTokProfileHighlightSyncFailure("highlights_sync_failed")
    target = await repo.get_owned_profile_highlight_sync_target(job["user_id"], profile_id)
    if target is None:
        raise TikTokProfileHighlightSyncFailure("highlights_sync_failed")
    # A provider collection belongs to a username, which can be reassigned.
    # Resolve it through the existing no-cookie stable-account scanner before
    # either claiming execution or persisting any provider response.
    scanner = scanner or TikTokProfileScanner()
    try:
        resolved_profile = await asyncio.to_thread(scanner.resolve_profile, target["username"])
    except Exception as exc:
        raise TikTokProfileHighlightSyncFailure("profile_identity_unavailable") from exc
    if not resolved_profile or resolved_profile.user_id != target["platform_account_id"]:
        raise TikTokProfileHighlightSyncFailure("profile_changed")
    if not await repo.claim_profile_highlight_sync_execution(job["id"], job["user_id"], profile_id):
        # Do not repeat a future external request merely because a message was
        # redelivered after a process interruption.
        raise TikTokProfileHighlightSyncFailure("highlights_execution_indeterminate")

    await repo.update_job_progress(job["id"], 10)
    provider = provider or DisabledProfileHighlightsProvider()
    try:
        result = await provider.discover_highlights(resolved_profile.username)
    except TikTokProfileHighlightSyncFailure:
        raise
    except TimeoutError as exc:
        raise TikTokProfileHighlightSyncFailure("highlights_provider_unavailable") from exc
    except Exception as exc:
        raise TikTokProfileHighlightSyncFailure("highlights_provider_unavailable") from exc

    snapshots = _validate_discovery_result(result)
    await repo.update_job_progress(job["id"], 35)
    collections_created = collections_refreshed = memberships_created = memberships_refreshed = 0
    summary = {
        "collections_observed": len(snapshots), "collections_created": 0,
        "collections_refreshed": 0, "collections_incomplete": result.incomplete_collections,
        "collections_failed": result.failed_collections, "memberships_created": 0,
        "memberships_refreshed": 0,
        "outcome": _discovery_outcome(
            len(snapshots), result.incomplete_collections, result.failed_collections,
        ),
    }
    for index, snapshot in enumerate(snapshots, start=1):
        try:
            reconciliation = await repo.reconcile_owned_highlight_collection_snapshot(
                job["user_id"], profile_id, snapshot,
            )
        except Exception as exc:
            summary.update(
                collections_created=collections_created,
                collections_refreshed=collections_refreshed,
                memberships_created=memberships_created,
                memberships_refreshed=memberships_refreshed,
                outcome="partial_failure" if index > 1 else "failed",
            )
            await repo.update_profile_highlight_sync_checkpoint(
                job["id"], summary, 35 + (60 * (index - 1) // max(1, len(snapshots))),
            )
            raise TikTokProfileHighlightSyncFailure("highlights_reconciliation_failed") from exc
        collections_created += int(reconciliation.collection_created)
        collections_refreshed += int(not reconciliation.collection_created)
        memberships_created += reconciliation.memberships_created
        memberships_refreshed += reconciliation.memberships_refreshed
        summary.update(
            collections_created=collections_created,
            collections_refreshed=collections_refreshed,
            memberships_created=memberships_created,
            memberships_refreshed=memberships_refreshed,
            outcome=_discovery_outcome(
                len(snapshots), result.incomplete_collections, result.failed_collections,
            ),
        )
        await repo.update_profile_highlight_sync_checkpoint(
            job["id"], summary, 35 + (60 * index // max(1, len(snapshots))),
        )

    return summary
