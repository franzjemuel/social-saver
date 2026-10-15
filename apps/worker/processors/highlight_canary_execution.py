"""Durable, no-repeat execution of the two paid Highlight canary operations."""
from __future__ import annotations

from dataclasses import dataclass

from providers.tiktok.apify_highlight_canary import (
    ApifyAsyncHighlightActorClient,
    ApifyCanaryIndeterminate,
    ApifyCanaryKnownFailure,
)


CANARY_TOTAL_BUDGET_MICRO_USD = 650_000
CANARY_DISCOVERY_RESERVATION_MICRO_USD = 100_000
CANARY_STORY_RESERVATION_MICRO_USD = 550_000


class HighlightCanaryExecutionFailure(RuntimeError):
    """Safe terminal category; caller must not let PGMQ retry paid work."""

    def __init__(self, code: str, *, authorization_status: str):
        super().__init__(code)
        self.code = code
        self.authorization_status = authorization_status


@dataclass(frozen=True)
class HighlightCanaryExecutionPolicy:
    provider_namespace: str
    total_budget_micro_usd: int = CANARY_TOTAL_BUDGET_MICRO_USD
    discovery_reservation_micro_usd: int = CANARY_DISCOVERY_RESERVATION_MICRO_USD
    story_reservation_micro_usd: int = CANARY_STORY_RESERVATION_MICRO_USD

    def __post_init__(self) -> None:
        if (
            not self.provider_namespace
            or self.total_budget_micro_usd != CANARY_TOTAL_BUDGET_MICRO_USD
            or self.discovery_reservation_micro_usd <= 0
            or self.story_reservation_micro_usd <= 0
            or self.discovery_reservation_micro_usd + self.story_reservation_micro_usd
            > self.total_budget_micro_usd
        ):
            raise ValueError("invalid Highlight canary execution policy")

    def limit_for(self, operation: str) -> int:
        if operation == "collection_list":
            return self.discovery_reservation_micro_usd
        if operation == "collection_items":
            return self.story_reservation_micro_usd
        raise ValueError("invalid Highlight canary operation")


class HighlightCanaryExecutor:
    """Binds Actor dispatch to one job's durable reservation ledger.

    A redelivered queue job may poll/fetch a receipted run, but it can never
    create a replacement run. A run without a persisted receipt is treated as
    indeterminate rather than risking another charge.
    """

    def __init__(self, repo, job, profile_id, policy: HighlightCanaryExecutionPolicy):
        self._repo = repo
        self._job = job
        self._profile_id = profile_id
        self._policy = policy

    async def invoke(
        self,
        operation: str,
        payload: dict[str, object],
        client: ApifyAsyncHighlightActorClient,
    ) -> list[dict[str, object]]:
        limit = self._policy.limit_for(operation)
        invocation = await self._repo.reserve_owned_highlight_provider_invocation(
            self._job["user_id"],
            self._job["id"],
            self._profile_id,
            provider_namespace=self._policy.provider_namespace,
            operation=operation,
            maximum_micro_usd=limit,
            execution_key=operation,
        )
        if invocation is None:
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_budget_exhausted", authorization_status="indeterminate",
            )

        status = invocation["status"]
        receipt = invocation.get("provider_run_receipt")
        if status in {"dispatched", "succeeded"}:
            if not isinstance(receipt, str) or not receipt:
                raise HighlightCanaryExecutionFailure(
                    "highlight_canary_receipt_unavailable", authorization_status="indeterminate",
                )
            return await self._collect_existing(invocation, receipt, client)
        if status != "reserved":
            if status == "started":
                await self._finish(invocation, "indeterminate", error_code="dispatch_receipt_missing")
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_invocation_not_reusable", authorization_status="indeterminate",
            )

        claimed = await self._repo.claim_owned_highlight_provider_invocation(
            self._job["user_id"], self._job["id"], invocation["id"],
        )
        if claimed is None:
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_invocation_not_reusable", authorization_status="indeterminate",
            )
        try:
            receipt = await client.start_run(payload, maximum_micro_usd=limit)
        except ApifyCanaryKnownFailure:
            await self._finish(claimed, "failed_known", error_code="provider_rejected")
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_provider_rejected", authorization_status="failed_known",
            ) from None
        except ApifyCanaryIndeterminate:
            await self._finish(claimed, "indeterminate", error_code="dispatch_indeterminate")
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_dispatch_indeterminate", authorization_status="indeterminate",
            ) from None

        dispatched = await self._repo.mark_owned_highlight_provider_invocation_dispatched(
            self._job["user_id"], self._job["id"], claimed["id"], receipt,
        )
        if dispatched is None:
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_receipt_unavailable", authorization_status="indeterminate",
            )
        return await self._collect_existing(dispatched, receipt, client)

    async def _collect_existing(self, invocation, receipt: str, client: ApifyAsyncHighlightActorClient):
        try:
            rows = await client.collect_run(receipt)
        except ApifyCanaryKnownFailure:
            await self._finish(invocation, "failed_known", error_code="run_failed")
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_provider_failed", authorization_status="failed_known",
            ) from None
        except ApifyCanaryIndeterminate:
            await self._finish(invocation, "indeterminate", error_code="polling_indeterminate")
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_provider_indeterminate", authorization_status="indeterminate",
            ) from None
        if invocation["status"] == "succeeded":
            # A redelivered job can re-read the completed dataset by the
            # durable receipt, but it must not attempt a second terminal
            # ledger transition (or a replacement paid Actor run).
            return rows
        finished = await self._finish(invocation, "succeeded")
        if finished is None:
            raise HighlightCanaryExecutionFailure(
                "highlight_canary_receipt_unavailable", authorization_status="indeterminate",
            )
        return rows

    async def _finish(self, invocation, status: str, *, error_code: str | None = None):
        return await self._repo.finish_owned_highlight_provider_invocation(
            self._job["user_id"], self._job["id"], invocation["id"],
            status=status,
            receipt=invocation.get("provider_run_receipt"),
            error_code=error_code,
        )
