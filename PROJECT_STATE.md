# Project state

Updated 2026-09-30 UTC (2026-09-29 America/New_York).

## Current verified status (supersedes historical entries below)

- PR #2 and PR #4 are merged. PR #4 head was
  614a76e8c83ea9bd5440f619d2e88e6e0906c383; its final run 36665622112 passed
  test, containers and integration. Merge commit is
  dacf421f145c74d208f1e9c0e69a4a0a42e47248.
- Merged-main run https://github.com/franzjemuel/social-saver/actions/runs/36665893782
  passed test, installed-wheel imports, all three container builds and the real
  disposable Postgres/PGMQ worker gate. This proves the test-job path, not
  Instagram extraction, Telegram delivery or hosted Supabase/R2 behavior.
- The clean local clone was fast-forwarded to GitHub main, preserving remote
  edits. No open PRs or issues were returned before this documentation update.
- Local ./scripts/verify.sh passed: 99 tests, five database tests skipped,
  compile/AST, migration chain 001–020, readiness and Compose validation.
  Docker builds were not retried; merged-main CI is the build authority.
- Main protection requires test and containers, strict up-to-date checks and PRs,
  including admins. Integration passed but is not yet a required protected check.
- User now authorizes continuing staging provisioning, superseding the earlier
  pause. No paid plan or production deployment is authorized.
- Railway dashboard was authenticated on a trial and initially showed zero
  projects. An attempted Empty Project selection unexpectedly created project
  sincere-respect (6627caf4-e1eb-4a0a-95d8-c890477ea521) and running sandbox
  21b4117a-b6ac-443a-bebf-67c8d7a00e98. No application code or secrets were
  deployed. Its Destroy action requires user confirmation; trial consumption
  has not been quantified. This is not the intended staging deployment.
- Supabase, R2, Telegram and Instagram staging resources remain unverified.
- The existing four-hour engineering automation remains ACTIVE and unchanged;
  pausing it would avoid duplicate usage while this chat is active.


## Completed
Telegram-first architecture; replaceable Instagram provider; Postgres/PGMQ; private R2 archive; separate bot/API/worker services; server-validated Telegram Mini App auth; tenant-scoped reads; shared save pipeline; idempotency/job status; archive browse/detail/download; entitlements/Stars; Saved Friends/story polling; rate limits/monitoring; session vault; Live reliability/security foundations; Railway staging scaffolding; CI/release gates; executable offline staging readiness report with migration floor 020 and service-specific secret boundaries; tenant-safe asynchronous archive deletion with worker-owned R2 cleanup; full asynchronous account deletion with immediate watch/archive disablement and shared-object-safe R2 purge.

## Current launch gate
1. Provision staging secrets/services.
2. Apply migrations once through controlled DB release.
3. Deploy worker, API, and bot.
4. Run staging preflight/canary.
5. Send a real authorized Instagram Reel to the bot.
6. Observe one queue job, one worker execution, Telegram delivery, and optional R2 archive.
7. Repeat with carousel and authorized Story/Saved Friend.

## Open risks
Real Instagram extraction/session behavior is not yet proven on staging. Live still needs network-level egress restrictions. Public beta needs finalized privacy/terms and explicit backup/log/payment retention periods. Pricing needs real traffic cost measurements.

## Next action
Use the persistent GitHub repository as source of truth, run `python scripts/staging-readiness.py`, then execute the hosted staging launch gate in `docs/staging-launch-v33.md`. Do not implement Facebook until it passes.

- v3.6 closed a Telegram-bot archive deletion regression: bot and Mini App now both queue worker-only physical R2 cleanup.

## v3.7 increment
- Added Railway config-as-code for bot, API, and worker with service-specific Dockerfiles and watch paths.
- Added API container build to CI, closing a deployment gate gap.
- Offline readiness now validates Railway service configs.
- Next gate remains real staging provisioning and Instagram end-to-end canary.

## v3.8 increment
- Added strict least-privilege secret validation per deployable service.
- Bot now explicitly fails staging validation if R2/provider credentials are injected; API fails if Instagram/session-vault credentials are injected.
- Added Railway acceptance contract in `docs/least-privilege-secrets-v38.md`.
- Next gate remains external staging provisioning and the real Instagram canary.

## GitHub takeover — 2026-09-28

- Imported the user-supplied v3.8 archive, not v3.1. All 20 migrations match the
  archive byte-for-byte. Preserved original release narrative in bootstrap history.
- Baseline failed editable installation: setuptools rejected multiple top-level
  packages. Added explicit build/package configuration; service imports now pass.
- Baseline tests: 87 passed, 1 stale authentication-name assertion failed. Replaced
  it with checks of every registered customer route's auth dependency and input
  schemas. Current tests: 88 passed on Python 3.13.
- Compile, AST, offline readiness and secret-free Compose validation pass.
- Consolidated duplicate CI workflows, included wheel/container import checks,
  restored executable scripts and excluded local secrets from build contexts.
- Local Docker builds attempted for all images; blocked because Docker engine is
  unavailable. GitHub container results are required before merging this branch.
- Hosted resources and credentials remain unidentified; no deployment occurred.
- Next runtime increments: isolated staging canary, then real Postgres JSON/PGMQ
  validation. See docs/staging-handoff.md before any online canary.

- Private GitHub repository created: https://github.com/franzjemuel/social-saver.
- Installed wheel imports pass outside the source checkout in a clean environment.
- User confirmed staging resources do not exist yet.
- GitHub private-repository branch protection was rejected by the account plan
  (HTTP 403); PR/check policy is procedural until an eligible plan is available.
- First GitHub run passed all 88 tests but exposed a Compose-version difference:
  `.env` was still required despite `--no-env-resolution`. Verification now selects
  `.env.example` explicitly via a Compose environment-file override.


## Iteration 2 — isolated staging canary (review branch)

- Build/workflow PR #1 merged after GitHub passed 88 tests, wheel verification,
  all three Linux container builds, service imports and FFmpeg availability.
- Isolated queue probe uses a private UUID-named queue and unconditional database
  rollback; no claims from media_jobs. R2 uses unique keys and failure cleanup.
- Refuses non-staging execution before network checks. Offline readiness output
  now explicitly states that hosted services have not been verified.
- 99 tests pass locally, including 11 canary safety regression cases; compile,
  readiness and Compose pass. Hosted queue/storage checks remain unrun.
- User explicitly paused Railway provisioning to finish GitHub first. No staging
  project or service was created; do not resume provisioning without user steering.
- Next runtime priority remains database JSON serialization and a real PGMQ/worker
  integration test, before provisioning and real Instagram delivery.

## Repository visibility and protection

The user authorized making the repository public to enable branch protection.
Gitleaks 8.30.1 found no secrets in committed history before publication. Repository
is now public; main requires passing test/container checks and a PR, including for
admins, and disallows force pushes/deletions. This supersedes the earlier private
repository plan limitation.

## Issue #3 — Postgres JSON round-trip (2026-09-29, work in progress)

- Working only from /Users/franz/Documents/GitHub/social-saver. Clean local main
  matched GitHub at d1d8c37 before editing; PR #2 remains open at 9b188a8 with green CI.
- Reviewed PR #2 and based fix/postgres-json-roundtrip on its head, so its serialized
  canary payload can be adapted with the database codec fix. Neither PR was merged.
- Applied per-connection json/jsonb codecs and an explicit text cast for the
  canary's pre-encoded JSON. No migrations or application feature logic changed.
- Added a disposable real Postgres/PGMQ gate for migration replay, codecs, tenant
  job lookup, dead letters, canary rollback and the actual worker's test-job path.
- Local: 99 tests passed, 5 database tests skipped; compilation and migration
  integrity checks passed. All three local Docker builds timed out waiting for
  the daemon. A clean installed-wheel check failed locally because the disk is full.
- GitHub run 36576459256 at 8d83d57 passed the test job (including installed-wheel
  verification) and all three container builds. The new integration job failed:
  migration 011 attempts to DROP INDEX for a UNIQUE constraint owned by watches.
  Migration replay reached 010; none of the five integration test bodies ran.
- Migration 011 contains a second equivalent constraint-index drop for
  watch_deliveries. Proposed fix: drop both constraints via ALTER TABLE instead.
  No migration edits applied: awaiting an explicit exception to AGENTS.md's
  imported-migration immutability rule. A later migration cannot unblock fresh
  replay because execution stops at 011.
- PR #4 is open against PR #2's branch; the real queue/worker round trip remains
  unproven. No merge or production deployment occurred.
- No v3.9–v3.11 provisioning/release-plan files exist in fetched GitHub branches.
  Do not substitute an older ZIP or claim that absent tooling was executed.
- No new hosted resources, secrets, deployments or real Instagram/Telegram/R2
  acceptance tests. Public GitHub visibility remains as explicitly authorized.

## Approved migration correction and handoff — 2026-09-29

- Franz explicitly approved the exception to imported-migration immutability.
  Migration 011 now drops the two UNIQUE constraints with ALTER TABLE instead
  of attempting to drop their backing indexes. All other migrations unchanged.
- After the correction, local verification passed: 99 tests, compilation, AST,
  migration-chain 001–020 and offline Compose/readiness checks. Five real-database
  tests skipped. Initial sandbox run blocked localhost sockets; approved retry
  passed. No real database replay or external delivery has passed yet.
- Next action: check CI on this correction in PR #4; fix any genuine replay or
  worker integration failures before staging deployment. Prior green test/build
  results do not establish success for this new commit.
- Continue from branch fix/postgres-json-roundtrip in the GitHub repository,
  not ZIPs. PR #4 is stacked on PR #2. Do not merge or deploy production.
- Staging resources are not provisioned. Once integration passes, establish
  dedicated Railway/Supabase/R2/Telegram/Instagram staging resources using
  existing deployment tooling and per-service secret separation. Never paste
  tokens or sessions into chat. First milestone remains a real public Reel
  delivered through Telegram; Live stays disabled.
- Franz requests one step at a time and an immediate stop when authorization
  is needed, without repeated polling while blocked.
