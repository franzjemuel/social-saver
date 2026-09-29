# Staging service matrix

| Service | Host | Command | Public HTTP | Persistent app disk |
|---|---|---|---|---|
| bot | Railway | `python -m apps.bot.main` | No, Telegram long polling | No |
| worker | Railway + `Dockerfile.worker` | `python -m apps.worker.main` | No | No |
| database | Supabase Postgres + PGMQ | managed | managed | managed |
| media | Cloudflare R2 Standard | managed | signed URLs only | managed |
| burst limiter | Upstash Redis | managed | backend only | managed |
| telemetry | Sentry | managed | SDK outbound | managed |

The worker intentionally uses ephemeral disk. Live segments are continuously uploaded to R2 and finalization can
redownload immutable segments. Do not make local disk part of the durability model.
