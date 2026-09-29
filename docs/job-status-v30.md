# v3.0 Mini App job status

## Goal
Turn the Mini App Save action into a useful progress flow without creating a second processing pipeline.

## Contract
`GET /v1/jobs/{job_id}` requires the same validated Telegram Mini App authentication as every customer endpoint. The repository query binds both `job_id` and the authenticated `app_user_id`. A missing job and another tenant's job both return `404 job_not_found`.

Customer response fields are deliberately limited to `id`, `job_type`, `status`, `progress`, `error_code`, and timestamps. The endpoint does not return the worker input, provider URLs, raw exception text, cookies, result payload, storage keys, or Telegram chat ID.

Statuses currently map to UI states:

* queued: Waiting in line
* running: Saving, show progress when nonzero
* completed: Saved and delivered to Telegram
* failed: Could not save, show a friendly error mapped from `error_code`

For beta, Lovable should poll at 2 seconds for the first 20 seconds, then every 5 seconds, and stop on completed/failed. Later, replace polling with SSE or WebSocket only if real usage justifies it.

## CORS
Set `API_CORS_ORIGINS` to the exact HTTPS origin of the deployed Mini App. Multiple origins are comma separated. Do not use `*`. The API explicitly allows the headers needed for Telegram authentication and save idempotency.

## Lovable implementation
After `POST /v1/save` returns `job_id`, keep the id in component state and poll `/v1/jobs/{job_id}` using the same `Authorization: tma <Telegram.WebApp.initData>` header. Render queued, running, completed, and failed states. Do not persist raw initData to localStorage.

## Backend boundary
Lovable owns presentation and polling only. FastAPI owns Telegram validation and tenant authorization. Postgres owns durable state. PGMQ and Railway workers own execution. Provider adapters own Instagram/Facebook behavior. R2 owns archived objects. FFmpeg remains worker-only.
