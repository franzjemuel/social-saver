# Operations baseline v1

Fast abuse control uses Upstash Redis sliding windows:
Free 6 requests/10s, Plus 20/10s, Pro 40/10s. These protect bursts only. Daily downloads, archive bytes,
watch slots, and Live minutes remain Postgres entitlements.

Provider concurrency starts at 2 simultaneous Instagram operations per worker process. This is intentionally
conservative. Scale workers before increasing provider concurrency.

Observe:
* PGMQ queue_length and oldest_msg_age_sec
* healthy worker heartbeat count
* provider session status
* watches in error
* Sentry exceptions tagged by service and job_type

Initial alert thresholds:
* queue oldest age > 120 seconds for 5 minutes: warning
* queue oldest age > 600 seconds: critical
* zero healthy workers for 2 minutes: critical
* Instagram session challenge: immediate operator action
* >10 watches in error or sharp growth: investigate provider health

Sentry is optional but recommended in production. `send_default_pii=False`; never attach Instagram session
settings, Telegram message bodies, signed R2 URLs, passwords, cookies, or raw provider responses.

Lovable can render a sanitized admin dashboard by consuming a future authenticated `/v1/admin/health` endpoint.
It must not connect directly to Redis, PGMQ, provider_sessions, or Sentry credentials.
