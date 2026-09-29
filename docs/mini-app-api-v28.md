# v2.8 Mini App API implementation

The v2.7 authentication contract is now an executable FastAPI service. This is
an intentionally small read-only first slice so staging can prove Telegram
identity and tenant isolation before browser writes are enabled.

## Request authentication

Send the exact `Telegram.WebApp.initData` value on every API request:

```
Authorization: tma <raw-init-data>
```

The API validates Telegram's signature and the five-minute freshness window,
then maps `telegram_user_id` to the internal `app_user_id`. The frontend never
sends an internal user UUID.

## Implemented routes

* `GET /health` - Railway readiness
* `GET /v1/me` - proves authenticated tenant mapping
* `GET /v1/dashboard` - archive/watch/job counts
* `GET /v1/archive?limit=25` - tenant-owned archive rows
* `GET /v1/watches?limit=100` - tenant-owned Saved Friends/watch rows

All list limits are bounded server-side. Write endpoints remain deliberately
out of v2.8 until staging proves read isolation with two Telegram accounts.

## Railway

Create a third service from the same repository using `Dockerfile.api`. It can
share `DATABASE_URL` and `TELEGRAM_BOT_TOKEN` with the bot, but it does not need
Instagram credentials, R2 secrets, FFmpeg, queue worker credentials, or the
session master key for this read-only slice.

Set `/health` as the Railway health check.

## Lovable handoff

Lovable owns presentation only. On Mini App startup:

```ts
const initData = window.Telegram?.WebApp?.initData;
const response = await fetch(`${API_BASE}/v1/dashboard`, {
  headers: { Authorization: `tma ${initData}` },
});
```

Do not persist raw initData in localStorage, logs, analytics, or error-reporting
breadcrumbs. Telegram now offers DeviceStorage and SecureStorage, but v2.8 does
not need either: initData is already supplied by Telegram for the current Mini
App launch and is short-lived by our backend policy.

## Staging acceptance test

1. Open the Mini App as Telegram account A and confirm `/v1/me` and dashboard.
2. Seed or create one archive row for A and confirm it appears.
3. Open as Telegram account B and confirm A's row never appears.
4. Copy A's raw initData after five minutes and confirm HTTP 401.
5. Alter one byte in A's initData and confirm HTTP 401.
6. Confirm the API container has no Instagram/R2/session-vault secrets.

Only after this passes should v2.9 add `POST /v1/save` and watch mutations.
