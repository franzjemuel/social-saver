"""Synthetic-only, worker-owned archival of one Highlight Story's media."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from core.highlight_media import HighlightStoryMediaService
from core.config import settings
from providers.base import (
    HighlightStoryMediaDescriptor,
    TerminalProviderError,
    SourceUnavailable,
)
from providers.tiktok.highlight_media_download import TikTokHighlightMediaDownloader


class TikTokHighlightStoryMediaFailure(RuntimeError):
    """Terminal, browser-safe failure for the disabled/manual-only boundary."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HighlightProviderExecutionPolicy:
    """Explicit, worker-only authority for a single paid provider request.

    This is intentionally not read from a queue payload or environment.  It
    makes the future live dependency opt-in, reserves a non-reusable budget
    slice before dispatch, and leaves ambiguous calls terminal rather than
    letting PGMQ redelivery purchase another invocation.
    """

    provider_namespace: str
    job_budget_micro_usd: int
    maximum_invocation_micro_usd: int
    operation: str = "collection_items"

    def __post_init__(self):
        if (
            not self.provider_namespace
            or self.operation != "collection_items"
            or self.job_budget_micro_usd <= 0
            or self.maximum_invocation_micro_usd <= 0
            or self.maximum_invocation_micro_usd > self.job_budget_micro_usd
        ):
            raise ValueError("invalid Highlight provider execution policy")


def _validate_descriptors(value):
    if not isinstance(value, tuple) or not value or len(value) > 20:
        raise SourceUnavailable("TikTok Highlight media descriptors are invalid")
    seen = set()
    for descriptor in value:
        if not isinstance(descriptor, HighlightStoryMediaDescriptor):
            raise SourceUnavailable("TikTok Highlight media descriptors are invalid")
        if (descriptor.position < 0 or descriptor.position > 9999
                or descriptor.asset_type not in {"video", "photo"}
                or not descriptor.candidates or len(descriptor.candidates) > 3
                or descriptor.position in seen):
            raise SourceUnavailable("TikTok Highlight media descriptors are invalid")
        seen.add(descriptor.position)
    return value


async def _finish_provider_invocation(repo, job, invocation, *, status, error_code=None):
    """Durably settle a dispatched external call before later media work.

    If the ledger write itself is ambiguous, do not continue to persistence or
    permit ordinary queue redelivery to reach the paid provider again.
    """
    try:
        finished = await repo.finish_owned_highlight_provider_invocation(
            job["user_id"], job["id"], invocation["id"], status=status,
            error_code=error_code,
        )
    except Exception:
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_receipt_unavailable") from None
    if finished is None:
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_receipt_unavailable")
    return finished


async def process_archive_highlight_story_media(
    job, repo, provider=None, downloader=None, media_service=None, execution_enabled=None,
    provider_execution_policy=None,
):
    """Persist available positions; descriptors remain in process memory only."""
    # This check comes before every ownership/provider lookup. A stale queue
    # message cannot consume provider work or fall into generic retry handling
    # while the server-owned feature remains disabled.
    if execution_enabled is None:
        execution_enabled = settings.tiktok_highlights_media_enabled
    if not execution_enabled:
        raise TikTokHighlightStoryMediaFailure("highlight_media_feature_disabled")
    payload = job.get("input") or {}
    profile_id, membership_id = payload.get("profile_id"), payload.get("membership_id")
    if not isinstance(profile_id, str) or not isinstance(membership_id, str):
        raise TikTokHighlightStoryMediaFailure("highlight_media_request_invalid")
    target = await repo.get_owned_highlight_story_media_target(job["user_id"], profile_id, membership_id)
    if target is None:
        raise TikTokHighlightStoryMediaFailure("highlight_media_not_found")
    if provider is None:
        # This increment deliberately registers no live descriptor source. If
        # an operator enables the route without injecting an approved provider,
        # fail once and leave the queue clean rather than treating this as a
        # transient TikTok/Instagram outage.
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_unavailable")
    if getattr(provider, "requires_execution_policy", False) and provider_execution_policy is None:
        # An adapter that can call a paid source cannot be accidentally used
        # through the otherwise useful synthetic processor seam.
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_policy_required")
    if provider_execution_policy is not None:
        if provider_execution_policy.provider_namespace != target["provider_namespace"]:
            raise TikTokHighlightStoryMediaFailure("highlight_media_provider_mismatch")
        budget = await repo.initialize_owned_highlight_provider_budget(
            job["user_id"], job["id"], profile_id, provider_execution_policy.job_budget_micro_usd,
        )
        if budget is None:
            raise TikTokHighlightStoryMediaFailure("highlight_media_provider_budget_unavailable")
        invocation = await repo.reserve_owned_highlight_provider_invocation(
            job["user_id"], job["id"], profile_id,
            provider_namespace=provider_execution_policy.provider_namespace,
            operation=provider_execution_policy.operation,
            maximum_micro_usd=provider_execution_policy.maximum_invocation_micro_usd,
        )
        if invocation is None:
            raise TikTokHighlightStoryMediaFailure("highlight_media_provider_budget_exhausted")
        claimed = await repo.claim_owned_highlight_provider_invocation(job["user_id"], job["id"], invocation["id"])
        if claimed is None:
            raise TikTokHighlightStoryMediaFailure("highlight_media_provider_invocation_unavailable")
    else:
        invocation = None

    try:
        raw_descriptors = await provider.resolve_story_media(
            username=target["username"], provider_namespace=target["provider_namespace"],
            provider_collection_id=target["provider_collection_id"], provider_item_id=target["provider_item_id"],
        )
    except TerminalProviderError:
        if invocation is not None:
            await _finish_provider_invocation(repo, job, invocation, status="failed_known", error_code="provider_known_failure")
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_failed") from None
    except SourceUnavailable:
        if invocation is None:
            # Preserve the existing synthetic/manual retry behavior. Only an
            # explicitly budgeted external invocation is made terminal to
            # avoid paying twice after an ambiguous provider outcome.
            raise
        await _finish_provider_invocation(repo, job, invocation, status="indeterminate", error_code="provider_ambiguous_failure")
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_indeterminate") from None
    except Exception:
        if invocation is not None:
            await _finish_provider_invocation(repo, job, invocation, status="indeterminate", error_code="provider_ambiguous_failure")
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_indeterminate") from None

    if invocation is not None:
        await _finish_provider_invocation(repo, job, invocation, status="succeeded")
    try:
        descriptors = _validate_descriptors(raw_descriptors)
    except SourceUnavailable:
        raise TikTokHighlightStoryMediaFailure("highlight_media_provider_invalid_response") from None

    # Only durable, non-expiring shape enters the database. Candidates are not
    # put into a job checkpoint, error message, or repository call.
    seeded = await repo.upsert_owned_highlight_story_media_assets(
        job["user_id"], profile_id, membership_id, descriptors,
    )
    if seeded is None:
        raise TikTokHighlightStoryMediaFailure("highlight_media_not_found")
    pending = await repo.list_owned_highlight_story_media_assets(job["user_id"], profile_id, membership_id)
    by_position = {item.position: item for item in descriptors}
    downloader = downloader or TikTokHighlightMediaDownloader()
    media_service = media_service or HighlightStoryMediaService(repo)
    attached = uploaded = reused = failures = 0
    with TemporaryDirectory(prefix=f"social-saver-highlight-{job['id']}-") as root:
        root_path = Path(root)
        for index, asset in enumerate(pending, start=1):
            descriptor = by_position.get(asset["position"])
            if descriptor is None or descriptor.asset_type != asset["asset_type"]:
                failures += 1
                await repo.record_owned_highlight_story_media_failure(
                    job["user_id"], profile_id, asset["id"], "descriptor_unavailable",
                )
                continue
            try:
                downloaded = await downloader.download(descriptor, root_path / f"{asset['id']}.asset")
                was_reused = await media_service.persist(
                    user_id=job["user_id"], profile_id=profile_id, asset_id=asset["id"], downloaded=downloaded,
                )
            except SourceUnavailable:
                failures += 1
                await repo.record_owned_highlight_story_media_failure(
                    job["user_id"], profile_id, asset["id"], "acquisition_or_storage_failed",
                )
                continue
            except Exception:
                failures += 1
                await repo.record_owned_highlight_story_media_failure(
                    job["user_id"], profile_id, asset["id"], "acquisition_or_storage_failed",
                )
                continue
            attached += 1
            reused += int(was_reused)
            uploaded += int(not was_reused)
            if hasattr(repo, "update_job_progress"):
                await repo.update_job_progress(
                    job["id"], min(99, int(index * 100 / max(1, len(pending)))),
                )
    pending_after = max(0, len(pending) - attached)
    return {
        "assets_discovered": len(descriptors), "assets_seeded": seeded["inserted"],
        "attached": attached, "uploaded": uploaded, "reused": reused,
        "failures": failures, "pending_after": pending_after,
        # The queue job completed its bounded work; this is deliberately not a
        # claim that every Story asset is privately archived.
        "outcome": "complete" if failures == 0 and pending_after == 0 else "partial",
    }
