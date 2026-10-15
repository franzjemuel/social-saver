"""Explicit staging operator command for one approved Highlight video canary.

It never contacts Apify or TikTok. It creates the single-use authorization and
normal PGMQ job atomically; the separately gated worker owns all paid work.
"""
from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from core.config import settings
from core.database import Database
from core.repository import Repository
from providers.tiktok.apify_highlights_provider import PROVIDER_NAMESPACE

CANARY_BUDGET_MICRO_USD = 650_000


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Queue one authorized staging Highlight video canary")
    command.add_argument("--user-id", required=True)
    command.add_argument("--telegram-chat-id", type=int)
    command.add_argument("--profile-id", required=True)
    command.add_argument("--membership-id")
    command.add_argument("--authorization-reference")
    command.add_argument(
        "--dry-run", action="store_true",
        help="Read only: verify whether the owned profile has an eligible video Story.",
    )
    return command


def _enabled() -> bool:
    return bool(
        settings.app_env == "staging"
        and settings.tiktok_highlights_media_enabled
        and settings.tiktok_highlights_live_provider_enabled
        and settings.tiktok_highlights_canary_enabled
        and settings.tiktok_highlights_canary_operator_enabled
    )


async def queue(args: argparse.Namespace) -> bool:
    # The dry run is a staging-only read. It deliberately works while every
    # live-provider gate is off, so an operator can discover the missing
    # membership prerequisite without making a network request or queueing
    # work. Queueing still requires all four server-owned gates.
    if args.dry_run:
        enabled = settings.app_env == "staging"
    else:
        enabled = _enabled()
    if not enabled:
        print("canary_queue=disabled")
        return False
    try:
        user_id = UUID(args.user_id)
        profile_id = UUID(args.profile_id)
    except ValueError:
        print("canary_queue=invalid_target")
        return False
    membership_id = None
    if args.membership_id:
        try:
            membership_id = UUID(args.membership_id)
        except ValueError:
            print("canary_queue=invalid_target")
            return False
    if not args.dry_run and (
        membership_id is None
        or not args.authorization_reference
        or args.telegram_chat_id is None
    ):
        print("canary_queue=invalid_target")
        return False
    database = Database(settings.database_url)
    await database.connect()
    try:
        repository = Repository(database.pool)
        if args.dry_run:
            if membership_id is not None:
                target = await repository.get_owned_highlight_story_media_target(
                    user_id, profile_id, membership_id,
                )
                eligible = target is not None and target["provider_namespace"] == PROVIDER_NAMESPACE and target["media_type"] == "video"
            else:
                eligible = await repository.has_owned_highlight_canary_candidate(
                    user_id, profile_id, PROVIDER_NAMESPACE,
                )
            print("canary_preflight=eligible" if eligible else "canary_preflight=no_eligible_membership")
            return bool(eligible)
        job = await repository.create_owned_highlight_story_media_canary_job(
            user_id,
            args.telegram_chat_id,
            profile_id,
            membership_id,
            provider_namespace=PROVIDER_NAMESPACE,
            authorization_reference=args.authorization_reference,
            budget_micro_usd=CANARY_BUDGET_MICRO_USD,
            queue_name=settings.queue_name,
        )
        if job is None:
            print("canary_queue=target_unavailable")
            return False
        print("canary_queue=" + ("queued" if job.created else "already_queued"))
        return True
    finally:
        await database.close()


def main() -> int:
    args = parser().parse_args()
    return 0 if asyncio.run(queue(args)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
