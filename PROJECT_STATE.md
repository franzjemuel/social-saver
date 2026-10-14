# Project state

## Native Telegram chat buttons — PR #37, review only

- The bot navigation PR adds real persistent reply-keyboard buttons beside
  the Telegram message composer: Open Archive, Add Profile, Save a Link and
  Help. Navigation/help messages also offer actual inline Web App buttons.
- The published archive home and Add Profile route were checked against the
  live Lovable website. The first is the homepage, and the second is the
  published /profiles/add route requiring Telegram Mini App context.
- The Mini App URL can be overridden per environment. Bot menu setup is
  best-effort and cannot disable the working link-to-MP4 route when Telegram
  temporarily rejects menu configuration.
- /archive with an Instagram URL retains its existing behavior; bare
  /archive opens the archive workspace. /myarchive and existing TikTok
  Quick Save are unchanged. Personal /status and notification outbox remain
  separate planned increments.
- Universal one-paste Instagram + TikTok links are the product direction;
  Stories, Highlights, photo carousels, and profile URLs need separate
  provider acceptance and may require an explicit profile-backup choice.
- No migration, paid Apify call, provider extraction change, image resizing,
  archive storage change, or production deployment. **Not merged/deployed.**
- See docs/bot-chat-and-universal-links.md for the user experience and
  capability roadmap.

## TikTok native Telegram video geometry — merged and staging-accepted

- A hosted short-share TikTok link successfully completed worker resolution,
  MP4 acquisition, and native Telegram delivery after PR #35, but the tester
  reports a square video on fullscreen playback while an earlier direct-link
  delivery appeared portrait. Both source downloads were logged at 807.05 KiB;
  matching byte sizes alone do not establish identical files or geometry.
- The worker previously forwarded the MP4 without re-encoding but did not pass
  video width, height, or duration to Telegram's sendVideo. Proposed fix: use
  bounded local ffprobe inspection of the actual downloaded MP4 to supply
  measured dimensions (including display rotation) to the existing native
  Telegram delivery interface; do not stretch square source files to 9:16.
- Sanitized worker diagnostics capture a short source-file SHA-256 prefix,
  source dimensions, and Telegram's returned dimensions to distinguish
  provider-format variation from Telegram presentation. No user identity,
  URLs, provider sessions, or Telegram bot tokens are logged.
- PR #36 merged and deployed to Railway staging on 2026-10-12. After the
  rollout, the tester reported that the previously square-playing 12.4 MB
  TikTok video now plays vertically inside Telegram. Source and receipt
  fingerprints were not manually reconciled; precise root cause remains inferred.
- No download-format change, transcoding, schema change, R2 write, paid Apify
  call, production deployment, or change to unrelated archive features.

## TikTok share-link Quick Save — merged and staging-accepted

- The Telegram quick-save bot recognizes TikTok-owned `/t/<share-code>/` links
  as well as `vm.tiktok.com` and `vt.tiktok.com` share links; it queues
  the URL without making an external network request during Telegram ingress.
- The worker resolves short links with a small bounded HTTP redirect sequence.
  Every hop is validated against exact HTTPS TikTok hostnames and only another
  short-share path or a concrete public video path is allowed. HTML response
  bodies are not downloaded or interpreted, and the resulting canonical video
  continues through the existing yt-dlp/Telegram MP4 path and at-most-once
  delivery latch.
- The tester-supplied `www.tiktok.com/t/ZPLSLxk8n/` has been externally
  observed redirecting to a concrete `@theyuh_lvsh/video/...` URL.
  Fixture tests reproduce this redirect without contacting TikTok.
- PR #35 merged and deployed to Railway staging on 2026-10-12. The tester
  confirmed the public TikTok /t/ link returns a native MP4 in the bot chat.
  TikTok may still block other redirects; broader acceptance remains incremental.
- TikTok photo/carousel chat delivery, Highlight media acquisition, and paid
  Apify activation remain separate work.

Updated for v3.8.

## TikTok Profile Highlights metadata foundation — merged

- Added provider-neutral, tenant-private collection and Story-membership schema
  records. Provider identifiers are explicitly namespaced and observed order is
  retained only as `provider_position`; it is not asserted TikTok-native.
- Snapshot persistence is atomic per validated collection and idempotently
  refreshes known memberships or their observed position. Missing memberships
  are deliberately retained and never marked absent by this foundation.
- A provider response's reported count is recorded separately from count
  consistency and independently verified upstream completeness. A matching
  count never alone marks a snapshot complete. Each response has a snapshot
  marker so future read projections can emit `observed_in_latest_snapshot` and
  distinguish memberships observed in the latest response from retained
  historical memberships; partial responses therefore never claim a complete
  current ordering.
- This adds no API route, worker dispatch, provider token, paid call, media
  acquisition, R2 attachment, automatic job, or deployment behavior.

## TikTok Profile Highlights manual synchronization — synthetic-only review candidate

- Adds a server-disabled-by-default, tenant-scoped manual Highlight-sync API and
  worker job contract. When disabled, the API returns a safe not-found response
  before it can enqueue work; no synthetic fixture or live provider is
  registered in hosted code.
- A job coalesces duplicate requests per tenant/profile inside the existing
  transaction and PGMQ enqueue boundary. Before discovery it records one
  durable execution claim; a redelivery after that claim is deliberately
  terminal/indeterminate rather than silently repeating a future paid provider
  request. This is not an exactly-once guarantee or a paid-Actor receipt.
- Test-only injected synthetic providers validate complete response structure
  before any collection reconciliation. They preserve provider positions,
  can retain successful collections from an honestly incomplete response, and
  never infer removals from missing memberships. Live Apify execution, provider
  tokens, budgets, automatic polling, media acquisition, routes enabled for
  customers, and deployment remain separate approval gates.
- Before the worker claims provider execution it resolves the archived
  username through the existing stable TikTok account scanner and rejects an
  unavailable or mismatched identity. Completed safe results distinguish
  complete, incomplete, and partial-failure collection observations; retained
  earlier collections are never rolled back merely because a later collection
  failed to enumerate.
- The worker independently checks the same disabled-by-default server flag
  before scanner resolution, provider selection, or its execution latch. A
  stale queued message therefore cannot cause any external request while the
  feature is off. Safe aggregate checkpoints are written after every committed
  collection, so a later database failure projects retained progress as
  partial failure without leaking provider details.
- Provider aggregate counters determine completed-job outcomes even for an
  empty snapshot set: failed-only observations are `failed`, incomplete-only
  observations are `incomplete`, and an empty successful discovery is
  `complete`.

## TikTok Highlight Story media archive — synthetic-only draft

- Migration 025 models ordered Story media positions backed by the existing
  private, SHA-256-deduplicated object store. Descriptor URLs and raw provider
  output are intentionally absent from database records, PGMQ payloads, safe
  API projections, and failure messages.
- Synthetic-provider tests exercise worker-owned attachment, partial recovery,
  retry-safe attachment, and tenant-authorized playback. Disabled or
  unregistered descriptor sources fail terminally and archive their queue
  message without generic provider retries or Telegram notices. A completed
  queue job reports `partial` whenever any position remains pending or fails;
  it never claims the full Story was archived. No paid Actor call or live
  TikTok acquisition is enabled.
- A hardened video-descriptor transport, durable paid-invocation ledger,
  provider licensing approval, and separately authorized one-Story staging
  acceptance remain prerequisites to activation.

## Profile archive foundation — 2026-10-04

- Added a provider-neutral, tenant-private profile archive schema: profiles,
  posts, multi-asset media, and historical engagement snapshots (migration 022).
- Added a metadata-only TikTok adapter with an off-event-loop, pinned `tt-dlp`
  profile scanner and Python `yt-dlp` per-post resolver, with a local 12-post
  development cap and no TikTok network calls in tests. Scanner-confirmed
  photo posts preserve their type and do not get invented video assets.
- Normalized TikTok post/profile fields include the stable account ID, caption,
  original publication time, media duration/thumbnail, and supported engagement
  metrics. A disappeared upstream post can be marked absent without deleting its
  preserved archive record.
- This is not a customer-facing UI, worker media-download implementation, or
  hosted acceptance result. No Instagram behavior, provider sessions, or
  deployment configuration changed.
- Local public benchmark acceptance for `@aliachin11` passed without cookies,
  authentication, or media downloads: tt-dlp resolved the expected numeric
  account ID and secUid, selected 12 posts, and yt-dlp enriched all 12.

## Profile archive read API — 2026-10-05

- Added Telegram-authenticated, tenant-scoped read routes for profile archive
  summaries, owned profile detail, and newest-first post lists with each post's
  latest engagement snapshot.
- Browser responses intentionally exclude profile/provider metadata, TikTok
  secUid, acquisition URLs, storage keys, and sessions. Foreign and missing
  profile IDs share the same 404 response.
- This is a read-only backend contract for a future frontend; it does not add
  profile import, media download, or hosted acceptance behavior.

## TikTok profile-import validation — 2026-10-05

- Added a Telegram-authenticated, worker-owned TikTok profile validation slice
  for the future Add Profile flow. It accepts only normalized public profile
  targets, atomically queues tenant-owned validation work, and safely coalesces
  duplicate taps and request retries.
- The browser can poll a tenant-scoped validation status and receive only a
  safe preview (platform, username, display name, optional preview avatar).
  Stable account IDs, secUid, raw provider data, and provider failures remain
  internal. Confirmation/import and frontend UI are intentionally not included.

## TikTok profile-import confirmation — 2026-10-05

- Added the second worker-owned Add Profile backend slice: an authenticated
  tenant can confirm only its completed TikTok validation preview, which queues
  an atomic, coalesced metadata import with a backend-owned initial limit of 12.
- Before persistence, the worker re-scans the public profile and compares its
  stable account identity with the validation result. A changed identity fails
  safely rather than importing a username-reused account. The same tenant's
  already archived account projects as ready without duplicate archive rows or
  re-import work.
- A completed preview has no separate wall-clock expiry in v1; confirmation is
  guarded by that fresh stable-identity scan immediately before any archive
  mutation.
- The existing profile-import status contract now projects validating,
  awaiting-confirmation, queued, importing, ready, and safe failures. It also
  supports restoration of the newest tenant-owned unfinished workflow. Browser
  responses exclude stable IDs, secUid, raw provider data, and job inputs.
- This increment is metadata-only: it does not add the frontend Add Profile UI,
  full-history/batch import, rescans, payments/quotas, or profile-media work.

## Profile archive media persistence — 2026-10-05

- Profile video assets can be queued only by their owning Telegram-authenticated
  tenant. A worker uses yt-dlp's native no-cookie download path for one concrete
  TikTok video into temporary storage, SHA-256 deduplicates into the existing
  private R2 object store, and attaches the shared object to the profile asset.
- Browser post lists expose only `has_archived_media`; playback mints a
  tenant-authorized short-lived URL without exposing provider URLs, storage keys,
  object IDs, or hashes. Photo/carousel persistence remains intentionally out of scope.
- Profile-media job UUID binding is covered by a real disposable-Postgres regression
  test, including targeted and bulk tenant-owned queue requests.
- Targeted requests now coalesce concurrent duplicate taps before queue delivery,
  and playback signing failures return a safe availability error rather than
  storage details. Native acquisition is bounded by yt-dlp's max-file option and
  local post-download validation.
- Single-video hosted acceptance is complete on staging. Migration 023 is applied,
  the corrected worker R2 credential passed a bounded write/read/delete probe, and
  the reviewed API/worker build completed the authenticated targeted path:
  queue, native TikTok acquisition, private R2 persistence, attachment, authorized
  signed playback, browser-readable video metadata, and a same-post idempotency
  retry. One of the 12 benchmark posts has persisted archived video; the other 11
  remain intentionally unprocessed. PR #16 is code merge-ready and its intended
  single-video operational acceptance is complete. Full-profile batches,
  photo/carousel persistence, and the separate frontend playback PR remain future
  work.
- Bulk profile-video backup now reuses that same tenant-owned queue, native
  no-cookie acquisition, SHA-256/R2 persistence, and playback path for every
  unpersisted video among the bounded initial archive posts. It skips already
  attached videos and preserves successful attachments when an individual video
  fails, returning only aggregate safe counts. Photo and carousel media remain
  metadata-only; frontend backup/progress controls and device downloads remain
  future work.
- Archived profile videos can also mint a tenant-authorized, short-lived R2
  attachment link for device saving. Playback remains a separate inline GET
  flow; the frontend Save control is intentionally still pending.

## Telegram prepared sharing for archived videos — 2026-10-05

- Added a tenant-authenticated prepared-message endpoint for one already
  archived TikTok MP4. It proves profile/post ownership before minting a
  short-lived ordinary R2 GET presign, keeps that URL server-side, and returns
  only Telegram's opaque prepared-message ID and expiry for a future Mini App
  `shareMessage` call.
- The API uses a lifespan-managed aiogram client, allows user chats, groups,
  and channels (not bot chats), and never queues acquisition or contacts a
  provider. Missing archived media, non-video posts, or unsuitable thumbnails
  return unavailable without contacting Telegram.
- Telegram requires an HTTPS JPEG thumbnail for `InlineQueryResultVideo`; this
  first slice uses only the existing server-stored provider thumbnail. TikTok
  metadata does not retain a thumbnail MIME type and that URL can independently
  expire, so explicitly non-JPEG formats return unavailable while opaque HTTPS
  URLs rely on Telegram validation. It does not add thumbnail persistence or
  generation, frontend controls, or hosted Telegram sharing acceptance.
- The returned share expiry is capped to the short-lived R2 presign even if
  Telegram returns a later prepared-message expiry. A future client must create
  a fresh prepared message after that time; Telegram media caching is not
  assumed.

## Completed

## Full TikTok profile sync — 2026-10-06

- Added an explicit tenant-scoped full-profile metadata sync that keeps Add Profile's
  fast initial 12-post import unchanged. A full tt-dlp scan indexes every currently
  discoverable post before sequential yt-dlp enrichment, with private durable job
  checkpoints so worker retries resume instead of restarting historical enrichment.
- Ordinary manual Sync now also scanner-indexes every newly discovered post ID while
  retaining its bounded 12-post rich refresh. Full Sync never auto-downloads MP4
  bytes; existing Back up videos remains the media-persistence boundary.
- The existing paginated posts API remains the browser contract for large profiles;
  frontend progressive loading/full-profile controls remain pending until hosted
  backend acceptance.

## TikTok photo/carousel asset metadata — 2026-10-10

- The pinned, no-cookie tt-dlp scanner now retains ordered public image-candidate
  groups for scanner-confirmed TikTok photo posts. Import, manual sync, and full
  sync persist one internal `photo` asset record per safe candidate group; multi-image
  posts project as `carousel` while single-image posts remain `photo`.
- Candidate URLs are worker-only acquisition hints: browser/API projections still
  exclude asset source URLs and fallback candidates. This increment does not download
  images, write R2 objects, mint image playback URLs, or change existing video paths.
- A scanner refresh may update only an unattached asset position. Once a position has
  private archived bytes, its scanner metadata remains immutable so changed upstream
  candidate ordering cannot reinterpret the retained object. A future image-persistence
  increment should add a durable upstream image identity if it must reconcile reorders.
- Future image acquisition must validate resolved public addresses and every redirect
  hop before connecting; the metadata scanner rejects non-HTTPS, local, private, and
  malformed literal-host candidates but performs no network acquisition itself.

## TikTok photo/carousel private persistence — 2026-10-10

- The worker can now process unpersisted ordered `photo` asset rows through the
  existing tenant-owned profile-media queue. It uses a dedicated no-cookie HTTPS
  downloader with public-IP DNS resolution/pinning, per-redirect validation,
  bounded candidate fallback, byte-size/time limits, and JPEG/PNG/WebP byte
  validation before SHA-256-deduplicated private R2 persistence.
- Carousel positions are processed independently. Existing attachments are skipped;
  a failed position remains unarchived and is selected on a later retry, while already
  persisted positions remain intact. Browser projections still expose only the
  aggregate `has_archived_media` state, never candidate or storage URLs.
- Each candidate has an end-to-end wall-clock deadline and strict bounded HTTP/1.1
  framing validation, including redirect hops, response headers, chunk framing, and
  trailers. A failed database record write never deletes a content-addressed R2 key
  based on a non-atomic lookup; it remains safely retryable until a future durable
  storage reconciler can prove it is unreferenced.
- This is offline-tested implementation evidence only. No real photo/carousel hosted
  acquisition, R2 persistence, image playback, or frontend rendering acceptance has
  been performed in this increment.

## TikTok photo/carousel private playback status — 2026-10-10

- Archive post projections now expose safe counts for ordered photo assets
  (`total`, `persisted`, and `pending`) plus a derived not-started/partial/complete
  state. These values are derived from durable asset rows and non-deleted stored
  objects, not from a post's declared media type.
- An authenticated owner can list safe, ordered photo-position state and request a
  short-lived private R2 read URL only for one persisted position. The API never
  projects acquisition candidates, storage keys, object IDs, hashes, or URLs for
  pending/deleted positions; signing responses are private/no-store.
- This backend contract does not add a frontend image viewer, browser-triggered
  acquisition, or hosted photo playback acceptance.

## Owner Telegram delivery of archived photos — candidate, not hosted

- The proposed first delivery slice queues a tenant-owned worker job for one
  **complete** persisted TikTok photo set of 1–10 JPEG/PNG positions. The worker
  reads private R2 objects into a temporary directory and uses Telegram's native
  `sendPhoto` (one image) or `sendMediaGroup` (2–10 images), retaining carousel
  order. It never gives the browser a storage key, provider URL, signed URL, or
  destination chat ID.
- The destination is derived from the server-verified Telegram Mini App identity,
  as with existing worker deliveries. This repository has no separate durable
  bot-DM authorization table; a Bot API refusal (for example, the user has not
  started the bot) is a sanitized terminal job failure. Introducing a durable
  bot-chat authorization record requires a later migration and review.
- This candidate is intentionally not hosted acceptance evidence. It does not
  support partial sets, WebP conversion, carousels over ten images, browser ZIP
  downloads, or cross-chat native sharing.
- Telegram does not accept a caller idempotency key for `sendPhoto` or
  `sendMediaGroup`. Before the worker makes that side effect it writes an
  internal attempt marker; an unconfirmed marker is never automatically resent.
  A confirmed marker lets a retry complete the job without another send. This is
  a bounded at-most-once policy, not exactly-once delivery: a crash between
  Telegram acceptance and durable confirmation may leave delivery unknown and
  requires an explicit user retry after review.

## Staged TikTok image framing diagnostics — pending review

- A review-only diagnostic increment adds safe worker events for HTTP framing
  rejections: an opaque correlation value, provider, candidate attempt, and a
  bounded reason code. It never logs acquisition URLs, response headers or
  bodies, cookies, or credentials.
- A follow-up review-only correction accepts repeated non-critical response
  fields while still requiring exactly one `Content-Length`,
  `Transfer-Encoding`, and redirect target. It preserves strict body framing
  and avoids treating ordinary repeated CDN fields as an acquisition failure.
- The strict parser, bounded candidate fallback, retry behavior, and
  customer-facing error strings are unchanged. No hosted retry or deployment
  has been performed for this diagnostic increment.

## Manual TikTok profile sync — 2026-10-06

- Added a worker-owned, tenant-scoped manual sync queue and safe status projection.
  A full tt-dlp post-ID scan reconciles removal/restoration, while yt-dlp metadata
  enrichment remains bounded to the initial 12-post window. No scheduling or
  automatic media backup is added; the frontend Sync button remains pending.

Telegram-first architecture; replaceable Instagram provider; Postgres/PGMQ; private R2 archive; separate bot/API/worker services; server-validated Telegram Mini App auth; tenant-scoped reads; shared save pipeline; idempotency/job status; archive browse/detail/download; entitlements/Stars; Saved Friends/story polling; rate limits/monitoring; session vault; Live reliability/security foundations; Railway staging scaffolding; CI/release gates; executable offline staging readiness report with migration floor 020 and service-specific secret boundaries; tenant-safe asynchronous archive deletion with worker-owned R2 cleanup; full asynchronous account deletion with immediate watch/archive disablement and shared-object-safe R2 purge.

## Current launch gate
1. Provision staging secrets/services.
2. Apply migrations once through controlled DB release.
3. Deploy worker, API, and bot.
4. Run staging preflight/canary.
5. Send a real authorized Instagram Reel to the bot.
6. Observe one queue job, one worker execution, Telegram delivery, and optional R2 archive.
7. Repeat with carousel and authorized Story/Saved Friend.

## Public Stories via Apify — 2026-10-01

- Added `/stories @public_username`, queued through the existing Postgres/PGMQ worker path.
- The worker calls the tested no-login Apify Stories Actor with a per-run cost cap,
  downloads temporary CDN media immediately, and delivers it through Telegram.
- `APIFY_API_TOKEN` is worker-only and is rejected by strict bot/API secret checks.
- Hosted Telegram delivery remains unverified until the worker token is configured and deployed.
- Hosted `/stories` acceptance passed: the bot delivered active public Story media
  through Apify and Telegram, and the user confirmed the files were forwardable.
- Automatic public Story watches now use Apify monitoring mode, establish a first-poll
  baseline, deduplicate in Postgres, and fan new media out to every matching tenant watch.
- Added a deployment-owned Telegram admin allowlist. Admins can create unlimited watches
  without changing Free/Plus/Pro limits for other accounts; `/id` exposes only the caller's ID.

## Open risks
Real Instagram extraction/session behavior is not yet proven on staging. Live still needs network-level egress restrictions. Public beta needs finalized privacy/terms and explicit backup/log/payment retention periods. Pricing needs real traffic cost measurements.

## Next action
Use the persistent GitHub repository as source of truth, run `python scripts/staging-readiness.py`, then execute the hosted staging launch gate in `docs/staging-launch-v33.md`. Do not implement Facebook extraction until it passes. The next profile-archive increment should add worker-owned verified photo/carousel byte persistence after the metadata path is accepted.

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

## Hosted Reel failure and authenticated fallback — 2026-09-30

- Staging Supabase mnwlqeeksruyuvqowrdq has migrations 001–020 applied and verified
  through the existing release script; pgmq/pg_cron/pgcrypto and both queues exist.
- Railway services initially ran the default bot Dockerfile. The worker logs
  showed duplicate Telegram polling, with no database worker heartbeat. Railway
  says new services cannot opt into legacy Config as Code since 2026-08-28;
  use explicit Dockerfile.worker, Dockerfile.api and Dockerfile build settings.
- After user rebuilt worker/API, worker heartbeat was fresh (5 seconds old).
  Real job c7554fc3-e58d-4562-8182-410d3cd08db3 ran and failed with
  SOURCEUNAVAILABLE: Instagram public resolver failed: ClientGraphqlError.
  User received the Telegram failure message; no media was delivered.
- Public post/Reel provider had no authenticated fallback despite prepared
  service credentials. This branch adds fallback through the existing encrypted
  session manager and propagates the worker database pool through ProviderRouter.
- Session challenge/disabled/failed-login states and active cooldowns now block
  automatic login; initial unused sessions still permit their first login.
  Authenticated request errors persist sanitized stop/cooldown state.
- Local verify.sh: 111 passed, five database tests skipped; compile, AST,
  migration chain, readiness and Compose passed. Hosted fallback not tested yet.
