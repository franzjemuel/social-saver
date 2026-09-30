# Staging setup checklist

## Current resources

- Supabase: project `mnwlqeeksruyuvqowrdq` verified ACTIVE_HEALTHY by CLI.
  Existing release script applied migrations 001–020; remote history matches.
  Read-only query verified pgmq, pg_cron, pgcrypto and both job queues.
  Application jobs/users were both zero after release.
- Telegram: `@socialsaverapp_bot`, confirmed by user to be a test bot.
  Token and delivery are not yet verified.
- Instagram: user reports dedicated account ready. Worker login is unverified.
- Railway: private `social-saver-staging` project, `staging` environment.
  No application services deployed in this session.
- R2: not yet provisioned/verified. Archive acceptance can follow Reel acceptance,
  but existing staging environment checks require storage credentials.

## User dashboard work

1. Open Cloudflare R2. If activation asks for billing, payment or subscription
   terms, stop and review cost before accepting. No paid commitment is authorized.
2. Create private bucket `social-saver-staging`. Keep public access disabled.
3. Prepare two bucket-scoped S3 credentials: API Object Read only; worker Object
   Read & Write (including deletion). Keep values in a password manager until
   entering them directly into the matching Railway service variables.
4. Create empty Railway services before attaching a source or triggering a
   deployment. Names: `social-saver-bot`, `social-saver-api`,
   `social-saver-worker`. Use the existing staging project/environment.
5. Configure each service separately with the table below. Do not use a shared
   group containing worker secrets. Never paste secret values into chat.

## Service configuration

| Service | Repository config file | Secret variables |
|---|---|---|
| bot | /railway.bot.toml | DATABASE_URL, TELEGRAM_BOT_TOKEN |
| api | /railway.api.toml | DATABASE_URL, TELEGRAM_BOT_TOKEN, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET |
| worker | /railway.worker.toml | DATABASE_URL, TELEGRAM_BOT_TOKEN, worker-specific R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET, SESSION_MASTER_KEY, INSTAGRAM_SESSION_USERNAME, INSTAGRAM_SESSION_PASSWORD |

Set APP_ENV=staging, SENTRY_ENVIRONMENT=staging and SOCIAL_SAVER_ROLE to the
matching role on each service. Keep LIVE_ALLOW_MANUAL_SOURCE=false. API has
/health configured in its Railway TOML. Leave repository root directory blank.
SESSION_MASTER_KEY must be a valid Fernet key; engineering will prepare its
secure generation. Do not invent a password for that setting.

## Engineering work after resources are ready

- Verify exact GitHub release commit and all three CI checks.
- Install/use the official Supabase CLI and existing
  scripts/staging-db-release.sh against project mnwlqeeksruyuvqowrdq only.
  Inspect migration dry-run before applying. Verify PGMQ and migrations 001–020.
- Validate each service using scripts/check-staging-env.py --strict with its
  scoped environment. These scripts are repository tools; current Dockerfiles
  do not copy scripts/, so do not assume they are available in container shells.
- Deploy worker, API and bot through the existing Railway TOML files. No
  production deployment or paid plan approval is implied.
- Run existing session-health and staging-canary tools with appropriate secrets.
  Do not expose full environment dumps or signed URLs in evidence.
- User sends an authorized public Reel to the bot. Verify job, queue processing,
  terminal result and actual Telegram receipt, then optional archive/deletion.

Public Reel resolution currently uses public GraphQL extraction. Having an
Instagram account ready does not prove that this extraction path will work;
provider-session checks and actual public Reel delivery are separate tests.
