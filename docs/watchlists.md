# Watchlist engine v1

The scheduler does no Instagram networking. Supabase Cron runs `dispatch_due_watches()` once per minute.
That function only locks due rows, creates `poll_watch` jobs, sends them to PGMQ, and advances `next_poll_at`.
Python workers perform provider discovery.

Instagram beta uses a 15 minute default poll interval. First successful poll establishes a baseline and sends
nothing historical. Later polls compare a bounded recent-ID cursor and queue unseen posts through the normal
`resolve_media` pipeline. `watch_seen_items` and a unique delivery constraint make repeated polls idempotent.

Provider boundary:
* scheduler knows only `platform`, `target_key`, and cursor JSON;
* Instagram owns target resolution and discovery;
* Facebook Stories can later implement the same discovery contract;
* media download/delivery remains the existing provider pipeline.

Public Instagram endpoints are opportunistic. Do not market watches as guaranteed capture until authenticated
session support and a secondary resolver exist. Five consecutive discovery failures put a watch into `error`.

Supabase recommends Cron jobs stay short; this design keeps cron DB-only and network work in workers.
