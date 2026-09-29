# Railway staging release contract v2.5

## Services
Create two persistent Railway services from the same GitHub repository.

| Service | Dockerfile | Start process | Health path |
|---|---|---|---|
| social-saver-bot-staging | `Dockerfile` | `python -m apps.bot.main` | `/health` |
| social-saver-worker-staging | `Dockerfile.worker` | `python -m apps.worker.main` | `/health` |

Both processes now bind Railway's injected `PORT`. `/health` performs a bounded Postgres readiness probe and returns 503 if the database cannot be reached. It exposes no credentials, queue contents, user data, Instagram state, or storage keys.

Set the Railway healthcheck path to `/health` and timeout to 300 seconds for both services. Railway uses this check during deployment activation; it is not continuous monitoring, so Sentry plus the existing worker heartbeat/system health remain the runtime monitoring layer.

## Deployment order
1. Provision hosted Supabase staging and enable PGMQ.
2. Apply all migrations using `scripts/staging-db-release.sh`.
3. Provision a private R2 staging bucket with bucket-scoped read/write credentials.
4. Create a separate Telegram staging bot and Instagram test/service account.
5. Add secrets using Railway service/environment variables. Never commit `.env`.
6. Deploy the worker first. Confirm `/health` returns 200 and a fresh worker heartbeat exists.
7. Run `./scripts/staging-canary.sh` from a trusted shell with staging variables.
8. Deploy the bot. Confirm `/health` returns 200.
9. Send `/start`, then one public Instagram post URL through the real Telegram bot.
10. Verify exactly one job completes, media is delivered, and the archive object exists in R2.
11. Test Reel, carousel, then one authorized Saved Friend Story.
12. Do not enable customer Live recording yet.

## Railway configuration
Railway deprecated new `railway.toml`/`railway.json` Config as Code. Configure the first staging services in Railway, then capture the working project with current Infrastructure as Code commands:

```bash
railway config init
railway config pull
railway config plan
railway config apply
```

Commit the generated `.railway/railway.ts` only after reviewing it for accidental secret literals. Secrets stay in Railway variables.

## Release gate
Beta is blocked if either service healthcheck fails, the staging canary is not READY, Instagram session health is not healthy, tenant-isolation tests fail, or the real Telegram to queue to worker to R2 to Telegram media flow has not succeeded.
