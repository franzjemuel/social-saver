# Backlog

## P0 launch blockers
- [x] Create persistent GitHub repository (`franzjemuel/social-saver`).
- [x] Identify staging test bot: @socialsaverapp_bot (user confirmed).
- [ ] Configure and validate staging Telegram token in service secret stores.
- [x] Provision staging Supabase + PGMQ; verified hosted 2026-09-30.
- [ ] Provision private R2 + least-privilege API/worker credentials.
- [ ] Provision Railway bot/API/worker. Config-as-code is ready in v3.7; external project/service creation remains.
- [x] Apply migrations 001–020 and verify remote history.
- [ ] Pass full hosted staging canary.
- [ ] Complete real Reel -> queue -> worker -> Telegram test.
- [x] Configure worker-only Apify token and complete `/stories` -> worker -> Telegram acceptance test.
- [ ] Deploy and prove Apify-backed `/watchstories` automatic delivery for one new Story.
- [ ] Configure the requested staging Telegram account in `ADMIN_TELEGRAM_USER_IDS` and verify `/plan` reports Admin.
- [ ] Complete archive-on-save -> R2 -> authorized download test.
- [ ] Complete two-account tenant-isolation acceptance test.

## P1 private beta
- [ ] Mini App job progress UI.
- [ ] Archive UI and Saved Friends management UI.
- [x] Add tenant-scoped profile archive read API for future private frontend rendering.
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
- [x] Add provider-neutral private profile archive schema, bounded async-safe tt-dlp TikTok profile scanner, and yt-dlp per-post metadata normalizer (v3.9 foundation).
- [x] Add worker-owned, SHA-256-deduplicated private R2 persistence and authorized playback for profile archive videos, with real-Postgres UUID queue binding coverage and native no-cookie yt-dlp concrete-post acquisition.
- [x] Complete the one-video TikTok profile-media persistence/playback/idempotency acceptance using `docs/profile-archive-media-canary.md`; one of 12 benchmark posts now has private persisted video and the other 11 remain intentionally unprocessed.
- [x] Reuse the bounded profile-media worker path to back up all eligible imported TikTok videos while skipping already persisted assets; photo/carousel persistence remains unsupported.
- [x] Add tenant-authorized attachment download links for already archived profile videos; frontend Save control remains pending.
- [x] Add tenant-authorized Telegram prepared-message creation for an already archived TikTok MP4; frontend native-share control and hosted Telegram acceptance remain pending.
- [x] Add tenant-authenticated TikTok profile-target validation jobs and safe status previews for the future Add Profile flow.
- [x] Add profile-import confirmation and bounded worker-owned TikTok metadata import after validation; do not imply full media persistence.
- [x] Add manual worker-owned TikTok profile metadata sync with full-ID presence reconciliation and bounded enrichment; frontend Sync control remains pending.
- [ ] Build Add Profile UI (input, preview/confirm, and progress) in the frontend; existing archive browse/detail and playback remain separate.
- [ ] Add frontend video-backup/progress controls and later bounded resumable larger-history batches. See `docs/profile-archive-media-next-steps.md` for photo/carousel, rescan/removal, and historical-engagement design.
- [ ] Add a Mini App control that calls the prepared-message endpoint and then Telegram's native `shareMessage`, followed by one bounded hosted sharing acceptance.
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
- [x] Verify hosted connectivity, migrations and PGMQ.
- [ ] Complete hosted service provisioning and the authorized Reel acceptance test.

## Hosted Reel follow-up — 2026-09-30

- [x] Verify hosted migrations, PGMQ and actual worker heartbeat after Dockerfile fix.
- [x] Diagnose public Reel ClientGraphqlError from first hosted job.
- [x] Implement/test authenticated Reel fallback with session stop/cooldown guards.
- [ ] Pass fallback CI, review/release to staging, verify account session and retry
  one authorized Reel. Telegram media delivery remains unproven.


## Full profile archive follow-up — 2026-10-06

- Backend full-history TikTok indexing/enrichment is implemented on the review branch.
- Frontend still needs an explicit Full Sync/Complete Profile control plus progressive
  pagination through `GET /v1/profile-archives/{profile_id}/posts`.
- Full Sync remains metadata-only; automatic full-account MP4 backup is intentionally
  separate from this increment.
