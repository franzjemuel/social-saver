# Staging deployment v1

## Hosting decision
Use Railway for the bot and worker, hosted Supabase for Postgres/PGMQ, Cloudflare R2 Standard for media, Upstash
Redis for burst limiting, and Sentry for telemetry.

Do not create new `railway.toml` or `railway.json` files. Railway deprecated Config as Code for new services and
set a 2026-12-01 cutoff for legacy use. Set up the first two services in Railway, then capture the stable project
using Railway's current Infrastructure as Code commands: `railway config init`, `railway config pull`,
`railway config plan`, and `railway config apply`.

## One-person staging launch
1. Create a fresh Supabase staging project.
2. Link it with `supabase link --project-ref $STAGING_PROJECT_ID`.
3. Preview with `supabase db push --dry-run`; then use `scripts/staging-db-release.sh`.
4. Create an R2 Standard staging bucket with bucket-scoped credentials.
5. Create Upstash Redis and Sentry projects.
6. Create a separate Telegram staging bot.
7. Create `social-saver-bot-staging` on Railway from GitHub using `Dockerfile`.
8. Create `social-saver-worker-staging` from the same repo using `Dockerfile.worker`.
9. Enter variables from `deploy/staging.env.template` in service secrets, never Git.
10. Deploy worker first and run `python ops/production_preflight.py`.
11. Confirm `worker_heartbeats`, then run `python ops/integration_smoke.py`.
12. Deploy bot and test `/start`, public link saving, `/watch`, `/watchstories`, `/archive`, `/plan`.
13. Run the controlled HLS recorder only on media you are authorized to record.
14. Invite a small private tester group only after all gates pass.

## Release blockers
Block beta for migration drift, missing PGMQ, stale worker heartbeat, failed queue smoke test, failed R2
write/read/presign, tenant isolation failure, Instagram challenge state, or non-idempotent payment reconciliation.

## Cost floor as of September 2026
Railway Hobby has a $5 monthly minimum including $5 resource usage. Supabase Pro is $25/month and includes $10
compute credit, enough for one Micro instance at current pricing. R2 Standard includes 10 GB-month, 1M Class A,
10M Class B operations, then costs $0.015/GB-month, $4.50/M Class A and $0.36/M Class B with free Internet egress.

Use Railway Hobby + Supabase Free + R2 free tier for a private non-paying beta if limits fit. Before accepting
paying customers, move the database to Supabase Pro. A reasonable known base budget is about $30/month for
Railway Hobby + Supabase Pro before variable compute, Redis/Sentry upgrades, and storage.

## Lovable boundary
Lovable can later provide customer and admin dashboards. It does not replace Railway workers, FFmpeg, database
migrations, Redis, R2, queues, or authenticated provider sessions.
