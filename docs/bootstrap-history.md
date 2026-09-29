# Social Saver Bootstrap v1.5

Milestone 0 proves Telegram -> Postgres -> PGMQ -> worker -> Telegram before any Instagram extraction is enabled.

## Setup

1. Create a Supabase project and enable the `pgmq` extension.
2. Run `supabase/migrations/001_bootstrap.sql` in the SQL editor.
3. Create a Telegram bot with BotFather.
4. Copy `.env.example` to `.env` and fill `TELEGRAM_BOT_TOKEN` and the direct Postgres `DATABASE_URL`.
5. Create a Python 3.12 virtual environment and install the project.
6. Run `python -m apps.bot.main`.
7. In a second terminal run `python -m apps.worker.main`.
8. Send `/start`, then `/testjob` to the bot.

The queue is server-only. Do not expose `pgmq_public` to the browser.

## Definition of done

- `/start` is idempotent.
- `/testjob` inserts one database job and one queue message.
- Worker claims with a visibility timeout, completes, notifies Telegram, then archives.
- A completed job is safe to see again.
- Killing a worker after claim leaves the queue message available after the visibility timeout.


## Retry and dead-letter behavior (v0.2)

PGMQ `read_ct` is used as the delivery-attempt counter. Transient failures are left unarchived so the message becomes visible after its visibility timeout. After the configured maximum attempts, the worker records a `dead_letter_events` row, sends a compact copy to the durable `dead_letter_jobs` queue, marks the application job failed, and archives the original queue message. Long-running processors can extend visibility with `pgmq.set_vt`; the worker includes a heartbeat helper for this.

Run migration `002_retry_dead_letter.sql` after `001_bootstrap.sql`.

## v0.3 public Instagram spike

Beta A accepts only direct public Instagram `/p/`, `/reel/`, and `/tv/` URLs. The resolver uses instagrapi's public GraphQL path and does not accept customer Instagram credentials. Public web extraction is intentionally treated as replaceable and fallible. The next adapter can be added behind `ProviderRouter` without changing Telegram or jobs.


## v0.4 real media path

Paste a direct public Instagram `/p/`, `/reel/`, or `/tv/` URL into the bot. The bot now:

1. normalizes the URL and creates a `resolve_media` job;
2. resolves public media through the Instagram provider;
3. persists normalized media metadata;
4. streams assets into a per-job temporary directory;
5. sends photos/videos to Telegram, chunking albums at 10 items;
6. deletes temporary files automatically;
7. retries transient/provider failures but immediately terminates unsupported/private-style terminal errors.

Beta A intentionally does not accept profile URLs, Stories, private media, passwords, or cookies.


## v0.5 delivery and storage router

Hosted Telegram multipart uploads are capped at 50 MB for ordinary files. This build keeps a small safety margin and routes files up to 49 MB directly to Telegram. Larger originals are uploaded to a private Cloudflare R2 bucket and the user receives a one hour presigned GET URL.

R2 credentials remain server-side. The bucket must remain private. Presigned URLs are bearer tokens, so keep their lifetime short.

This is overflow storage, not yet the paid permanent archive. A later archive entitlement can reuse the same R2Storage adapter and move keys from `overflow/` into durable archive namespaces.


## v0.6 archive lifecycle

`/archive <instagram-url>` now creates a user-owned durable archive reference.
Physical R2 bytes are content-addressed by SHA-256 under `archive/`, so identical
public assets can be stored once while multiple users retain independent archive
ownership records.

Deletion is reference-aware: deleting one user's archive entry only removes the
R2 object when no active archive entry still references that object. Archive
reads must resolve ownership in Postgres before issuing a short-lived presigned
GET URL.

Temporary large-file delivery remains under `overflow/`. Configure the R2
lifecycle rule in `ops/r2-lifecycle.md` to expire that prefix after two days.
Do not apply expiration to `archive/`.

This build intentionally does not expose a public R2 bucket. Lovable may later
call authenticated archive APIs, but it must never receive R2 credentials or
construct storage keys itself.

## v0.7 archive UX
`/myarchive` lists the latest 10 owned items with Download and Delete controls. Downloads are ownership-checked and use 15-minute R2 URLs. Delete requires confirmation and removes physical bytes only when no active archive entry references them. This release also fixes a v0.6 indentation defect that left archive repository helpers outside the Repository class. The future Lovable API contract is in `docs/archive-api-contract.yaml`.

## v0.8 entitlements
Adds database-driven plans, subscription records, archive quota accounting, concurrency-safe reservations, `/plan`, and server-side archive gating. It also repairs the malformed v0.7 bot handler indentation so all handlers register inside `main()`.

## v0.9 Telegram Stars
Adds `/upgrade`, recurring 30-day Plus/Pro Stars invoices, pre-checkout integrity validation, idempotent successful-payment recording, entitlement activation, and `/paysupport`. Seeded Star prices are placeholders.

## v1.0 subscription lifecycle
Adds renewal reconciliation, preserved initial Telegram charge IDs, `/subscription`, safe `/cancelplan`, paid-through cancellation semantics, subscription audit records, and an expiry job. Plan switching remains intentionally disabled until the old Telegram auto-renewal can be canceled atomically.

## v1.1 watchlists
Adds `/watch`, `/watches`, `/unwatch`, entitlement-aware watch slots, Supabase Cron dispatch, PGMQ poll jobs, provider-neutral cursor state, Instagram profile discovery, first-poll baselining, seen-item deduplication, and automatic delivery through the existing media pipeline.

## v1.2 authenticated Instagram session foundation
Adds encrypted backend-only session persistence, session health/cooldown/challenge states, stable saved-settings reuse, authenticated fallback for watch discovery, and the first provider-neutral Instagram Story discovery adapter. Customer Instagram passwords are intentionally not accepted.

## v1.3 Story watch delivery
Adds authenticated Instagram Story resolution into the standard ResolvedMedia pipeline, `/watchstories`, `/watchboth`, separate Story/post cursors and deduplication, and automatic Story delivery through existing Telegram/R2 infrastructure. Existing watches remain posts-only.

## v1.4 production guardrails
Adds tier-aware Upstash Redis velocity limits, conservative Instagram worker concurrency, optional Sentry error/tracing instrumentation, PGMQ/worker/session/watch health reporting, `/status`, an ops health CLI, alert thresholds, and a Lovable operations-dashboard contract. Commercial quotas remain in Postgres.

## v1.5 bounded Live recorder
Adds provider-neutral LiveSource, monthly Live-minute reservation, a single-recorder beta lock, bounded FFmpeg stream-copy segmentation, continuous R2 segment upload, recording heartbeats/ledger, an FFmpeg worker Dockerfile, and a manual HLS test harness. Instagram viewer-side Live manifest discovery is intentionally left behind a resolver boundary until a reliable method is verified.

## v1.6 runtime integrity repair + Live resolver lab
Repairs Telegram handler registration so the bot no longer references Dispatcher state at module import, restores intended subscription/watch commands, adds `/lives`, MP4 finalization scaffolding, and adds an experimental positive-signal Instagram top-live probe while keeping `/watchlive` disabled until reliable detection is proven.

## v1.7 integration gate
Adds a reproducible Supabase-local integration path, real queue/worker smoke test, GitHub Actions verification,
runtime wiring tests, worker heartbeat persistence, and correct FFmpeg worker Compose configuration. It also restores
`finalize_live` dispatch, which the v1.6 runtime repair had not wired into the worker.

## v1.8 staging deployment package
Chooses Railway + hosted Supabase + R2 + Upstash + Sentry for private beta. Adds production preflight, staging secret template, migration release scripts, service matrix, current cost floor, beta scope and release blockers. Avoids Railway's deprecated Config as Code format.

## v2.0 Saved Friends autosave
Adds `/savefriend`, `/friends`, and `/removefriend`. Saved Friends are high-priority Story watches checked about once
per minute; unseen Stories are deduplicated, queued, delivered to Telegram, and automatically archived through the
existing R2 pipeline. The design remains provider-neutral so Facebook Stories can implement the same watch contract.


## v2.0 adaptive Saved Friends polling
Saved Friends now move between 60-second fast, 180-second warm, and 900-second idle polling based on observed Story activity. See `docs/adaptive-saved-friends-polling.md`.

## v2.1 tenant boundary

v2.1 fixes the archive repository runtime boundary, adds owner-scoped archive-object resolution before R2 URL minting, and closes the internal Supabase `public` schema to browser roles. See `docs/tenant-isolation-v1.md`.

## v2.2 Live reliability
Long-running Live jobs now use rolling PGMQ visibility leases, job-idempotent recording sessions, durable quota reservations, partial-capture reconciliation, and an expired-lease recovery command. See `docs/live-reliability-v2.md`.

## v2.3 security increment
Live recording now resolves network sources inside provider adapters rather than trusting customer-supplied manifests. HTTPS/public-network validation, sanitized provider headers, and an FFmpeg protocol allowlist are enforced. Manual sources are disabled by default. See `docs/live-source-security-v2.md`.

## v2.4 staging canary increment

v2.4 adds a destructive-safe staging canary that verifies Telegram identity, Postgres/PGMQ, migration state, queue round-trip, worker freshness, Instagram session health, and R2 write/read/delete before beta traffic is enabled. See `docs/staging-canary-v2.md`.

## v2.6 release safety

Before the first staging deployment, v2.6 normalized the Supabase migration chain to unique contiguous versions and added a GitHub Actions gate that runs the test suite and builds both production containers. See `docs/release-gate-v26.md`.
