# v2.9 Mini App Save

## Goal
Make the Mini App useful without creating a second downloader. `POST /v1/save` authenticates the Telegram user, validates the provider URL, applies the existing velocity limit, creates the same `resolve_media` job used by the bot, and sends it to the same PGMQ queue.

## API

`POST /v1/save`

Headers:
- `Authorization: tma <Telegram.WebApp.initData>`
- `Idempotency-Key: <random UUID generated per user action>`
- `Content-Type: application/json`

Body:
```json
{"url":"https://www.instagram.com/reel/SHORTCODE/"}
```

Accepted response (`202`):
```json
{"job_id":"uuid","status":"queued","platform":"instagram"}
```

Errors: `400 idempotency_key_required`, `401 invalid_telegram_auth`, `422 unsupported_url`, `429 rate_limited`, `503 api_not_ready`.

The idempotency key is unique per tenant. A frontend retry or double tap reuses the existing job rather than creating a second logical job. A duplicate queue message is harmless because workers archive messages for jobs already marked completed.

## Provider boundary
The HTTP contract accepts a URL, not `platform=instagram`. `core/save_requests.py` owns provider recognition. Adding Facebook later means registering its normalizer there and adding a Facebook provider to `ProviderRouter`; the Mini App contract does not change.

## Delivery
The Mini App uses the Telegram user's private chat ID and therefore delivers through the existing Telegram worker path. The API never downloads media itself. This keeps provider credentials, extraction, FFmpeg, R2 and queue processing out of the browser-facing service.

## Lovable implementation
Lovable can build the URL input, Save button, queued/success UI, Archive, Saved Friends, Usage and Upgrade screens. On Save it should create a UUID once, retain that UUID while retrying the same request, and call this endpoint with Telegram `initData`.

Do not put bot tokens, Instagram sessions, Supabase secret keys, R2 credentials or queue access in Lovable.

Suggested UI states: `Ready → Queuing → Sent to Telegram`. For 429, show a retry countdown from `Retry-After`. For 422, say the link is not currently supported.

## Staging acceptance test
1. Open the Mini App from the bot so Telegram supplies real `initData`.
2. Paste a public Instagram Reel and tap Save once.
3. Confirm one jobs row with `source_channel='mini_app'`.
4. Confirm the worker claims the job and Telegram receives the media.
5. Retry the identical HTTP request with the same Idempotency-Key. Confirm no second logical job exists.
6. Repeat with a new key. Confirm a new job is created.
7. Alter `initData`; expect 401.
8. Submit a non-supported domain; expect 422 and no job.
9. Exceed the plan velocity window; expect 429.

## Security note
Telegram documents that `initDataUnsafe` must not be trusted; the backend must validate raw `initData`. Keep the Mini App on one configured origin. Telegram's 2026 origin protection blocks Mini App methods from origins other than the configured Mini App domain by default.
