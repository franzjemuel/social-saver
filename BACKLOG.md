# Backlog

## P0 launch blockers
- [x] Create persistent GitHub repository (`franzjemuel/social-saver`).
- [x] Identify staging test bot: @socialsaverapp_bot (user confirmed).
- [ ] Configure and validate staging Telegram token in service secret stores.
- [ ] Provision Postgres/Supabase + PGMQ.
- [ ] Provision private R2 + least-privilege API/worker credentials.
- [ ] Provision Railway bot/API/worker. Config-as-code is ready in v3.7; external project/service creation remains.
- [ ] Apply migrations and pass staging canary.
- [ ] Complete real Reel -> queue -> worker -> Telegram test.
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
- [x] Merge canary isolation (PR #2).
- [ ] Validate canary against hosted PGMQ/R2.
- [x] Verify/fix Postgres JSON codecs with real database round-trip tests.
- [ ] Confirm hosted staging identifiers and secrets via hosting secret stores.
- [ ] Pin a tested dependency resolution for repeatable staging releases.

- [x] Publish repository with user authorization and enable enforced main-branch protection.

## Issue #3 execution

- [x] Implement per-connection JSON codecs and serialized-canary compatibility.
- [x] Add a real disposable Postgres/PGMQ/worker integration gate to CI.
- [x] Apply user-approved migration 011 correction: replace two constraint-index
  drops with ALTER TABLE DROP CONSTRAINT. Local verification passed; actual
  database replay still requires a passing integration run.
- [x] Pass real integration gate and queue/worker test-job round trip; PR #4
  merged as dacf421 with green main run 36665893782.
- [ ] Require integration in main branch protection before staging release.
- [x] Confirm unintended Railway sandbox is Destroyed (zero active sandboxes).
- [x] Establish private Railway project social-saver-staging and staging environment.
- [x] Pause duplicate engineering automation with user approval.
- [x] User created healthy Supabase project mnwlqeeksruyuvqowrdq.
- [ ] Verify hosted connectivity, migrations and PGMQ.
- [ ] Complete hosted service provisioning and the authorized Reel acceptance test.
