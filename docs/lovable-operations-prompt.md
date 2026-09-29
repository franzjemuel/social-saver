Build an internal Operations page for Social Saver. UI only. Consume an authenticated `/v1/admin/health` API.
Show queue depth, oldest queued job age, healthy workers, provider health, watch errors, and 24-hour error trend.
Use green/warning/critical states from backend-provided status. Do not expose Redis credentials, PGMQ access,
Instagram session data, Sentry DSN/admin tokens, R2 signed URLs, or raw error payloads.
