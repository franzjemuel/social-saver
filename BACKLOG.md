# Backlog

## P0 launch blockers
- [x] Create persistent GitHub repository (`franzjemuel/social-saver`).
- [ ] Provision staging Telegram bot/token.
- [ ] Provision Postgres/Supabase + PGMQ.
- [ ] Provision private R2 + least-privilege API/worker credentials.
- [ ] Provision Railway bot/API/worker. Config-as-code is ready in v3.7; external project/service creation remains.
- [ ] Apply migrations and pass staging canary.
- [ ] Complete real Reel -> queue -> worker -> Telegram test.
- [x] Configure worker-only Apify token and complete `/stories` -> worker -> Telegram acceptance test.
- [ ] Deploy and prove Apify-backed `/watchstories` automatic delivery for one new Story.
- [ ] Configure the requested staging Telegram account in `ADMIN_TELEGRAM_USER_IDS` and verify `/plan` reports Admin.
- [ ] Complete archive-on-save -> R2 -> authorized download test.
- [ ] Complete two-account tenant-isolation acceptance test.

## P1 private beta
- [ ] Mini App job progress UI.
- [ ] Archive UI and Saved Friends management UI.
- [x] Archive deletion and physical cleanup boundary.
- [x] Full account deletion pipeline.
- [ ] Finalize backup/log/payment retention periods and deletion UX copy.
- [ ] Privacy policy and terms.
- [ ] Measure costs and tune quotas/pricing.
- [ ] Operational alerts for queue lag, heartbeat, provider session and delivery failures.

## P2 hardening
- [ ] Recorder network egress isolation and real Live staging proof.
- [ ] Credential/session rotation runbook.
- [ ] Database backup/restore drill.
- [ ] Abuse/takedown/support workflow.

## P3 expansion
- [ ] Facebook provider adapter after Instagram launch gate.
- [ ] Facebook Stories through existing watch/archive contracts.

- [x] Enforce worker-only physical archive deletion from both Mini App and Telegram bot (v3.6).

- [x] Add GitHub-to-Railway config-as-code and build all three service images in CI (v3.7).

- [x] Enforce per-service least-privilege secret boundaries with a strict deployment gate (v3.8).

## Takeover findings (2026-09-28)

- [x] Repair package discovery that blocked every pip/container build.
- [x] Replace stale auth function-name assertion with actual route dependency checks.
- [x] Consolidate CI and add installed-wheel/container import verification.
- [x] Implement isolated staging canary and probe cleanup on a review branch.
- [ ] Merge canary isolation after review and validate against hosted PGMQ/R2.
- [ ] Verify/fix Postgres JSON codecs with real database round-trip tests.
- [ ] Confirm hosted staging identifiers and secrets via hosting secret stores.
- [ ] Pin a tested dependency resolution for repeatable staging releases.

- [x] Publish repository with user authorization and enable enforced main-branch protection.

## Issue #3 execution

- [x] Implement per-connection JSON codecs and serialized-canary compatibility.
- [x] Add a real disposable Postgres/PGMQ/worker integration gate to CI.
- [x] Apply user-approved migration 011 correction: replace two constraint-index
  drops with ALTER TABLE DROP CONSTRAINT. Local verification passed; actual
  database replay still requires a passing integration run.
- [ ] Pass the real integration gate (run 36576459256 failed at migration 011),
  prove the queue/worker round trip, and review stacked PR #4 after PR #2.
- [ ] Complete hosted service provisioning and the authorized Reel acceptance test.

## Hosted Reel follow-up — 2026-09-30

- [x] Verify hosted migrations, PGMQ and actual worker heartbeat after Dockerfile fix.
- [x] Diagnose public Reel ClientGraphqlError from first hosted job.
- [x] Implement/test authenticated Reel fallback with session stop/cooldown guards.
- [ ] Pass fallback CI, review/release to staging, verify account session and retry
  one authorized Reel. Telegram media delivery remains unproven.
