# v2.7 Telegram Mini App boundary

## Goal
Add a safe customer dashboard surface without exposing Postgres, R2, Instagram sessions, queue credentials, or Supabase secret keys to Lovable/browser code.

## Request authentication
The browser sends Telegram's raw `window.Telegram.WebApp.initData` in `Authorization: tma <raw-init-data>` to the Social Saver API. The API verifies the HMAC with the bot token, rejects data older than 5 minutes, extracts the Telegram user id, and maps it through `telegram_accounts` to the existing `app_user_id`.

Never authorize from `initDataUnsafe`, a username, an archive UUID, or a client supplied app user UUID.

## v1 API contract

### GET /v1/me
Returns identity and current plan summary.

### GET /v1/dashboard
Returns `archive_count`, `active_watch_count`, `active_job_count`, plan and quota summary. All queries derive `app_user_id` from verified Telegram identity.

### GET /v1/archive?cursor=...
Returns the caller's archive metadata only. Media download actions call the existing `get_owned_archive_object()` boundary and mint a short lived R2 GET URL only after ownership succeeds.

### GET /v1/watches
Returns the caller's Saved Friends/watch rows.

### POST /v1/watches
Accepts `{platform,target}`. The API creates a watch for the authenticated caller; it never accepts `user_id`.

### DELETE /v1/watches/{id}
Deletes/pauses only a watch owned by the authenticated caller.

## Lovable boundary
Lovable can build the Telegram Mini App UI: Home, Paste Link, Archive, Saved Friends, Usage and Upgrade. It may call the API with Telegram initData. It must not receive database credentials, Supabase secret/service-role keys, R2 keys, Instagram cookies/passwords, queue credentials, FFmpeg options, or raw provider session data.

## Backend boundary
The real backend owns Telegram verification, tenant mapping, authorization, queue submission, signed R2 URLs, provider sessions, billing truth, quotas, workers and FFmpeg.

## Recommended Lovable prompt
Build a mobile-first Telegram Mini App called Social Saver. Use Telegram WebApp SDK. On startup call `Telegram.WebApp.ready()` and send the raw `Telegram.WebApp.initData` only to our backend in an Authorization header using the `tma` scheme. Never use `initDataUnsafe.user.id` as authorization. Create five screens: Home with paste/save action and current usage, Archive with media cards and pagination, Saved Friends with Active/Quiet/Error states, Usage with plan limits, and Upgrade. Treat all server data as authoritative. Do not connect directly to Supabase tables and do not put any secret keys in frontend environment variables. Include loading, empty, expired-session and API-error states.

## Release tests
1. Valid initData resolves the correct Telegram identity.
2. One changed byte fails signature verification.
3. initData older than five minutes fails.
4. User A cannot retrieve User B archive/watch IDs.
5. API ignores/rejects any supplied `user_id` field.
6. R2 URL is minted only after owned-object lookup.
7. Browser bundle contains no backend/provider/storage secrets.
