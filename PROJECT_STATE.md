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
