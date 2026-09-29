# GitHub to Railway deployment contract, v3.7

## Goal
Make GitHub the deploy source for all three backend services without rebuilding every service for every commit.

## Railway services
Create three Railway services from the same GitHub repository and staging branch. Configure each service to use its matching config file:

| Service | Railway config | Dockerfile | Public domain |
|---|---|---|---|
| social-saver-api | `/railway.api.toml` | `Dockerfile.api` | yes |
| social-saver-bot | `/railway.bot.toml` | `Dockerfile` | no |
| social-saver-worker | `/railway.worker.toml` | `Dockerfile.worker` | no |

The repository is a shared Python monorepo, so do not set a Railway Root Directory. Each service needs shared `core/` and `providers/` code. Watch patterns instead limit unnecessary rebuilds.

## Deploy policy
Staging may autodeploy from a `staging` branch after CI passes. Production should remain manual until the real Instagram canary and rollback drill pass. Do not put secrets in any TOML file. Railway service variables hold credentials.

The API alone gets a Railway health check at `/health`. Bot and worker are long-running consumers and should be monitored using the existing heartbeat/system-health path rather than pretending they expose HTTP health endpoints.

## CI gate
CI now builds bot, worker, and API images. Previously the API Dockerfile was not built in CI, so a broken API container could reach deployment without an image-build gate.

## One-person launch sequence
1. Push this repository to GitHub.
2. Require the `Social Saver CI` workflow on the protected main branch.
3. Create a `staging` branch.
4. Connect all three Railway services to the same repository and staging branch.
5. Point each service to its config file above.
6. Add service-specific secrets from `scripts/check-staging-env.py`.
7. Apply migrations 001 through 020 to Supabase and create the durable PGMQ queue.
8. Deploy API, bot, worker.
9. Run `./scripts/staging-canary.sh`.
10. Run one real Instagram Reel through Telegram and verify optional R2 archive and deletion.

## Lovable boundary
Lovable remains only the Mini App UI. It can call the deployed API domain. It cannot replace Railway workers, Instagram session handling, PGMQ, FFmpeg, R2 signing/writes, or Telegram bot execution.
