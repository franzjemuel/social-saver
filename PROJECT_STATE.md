# Project state

Updated for v3.8.

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
