# Staging handoff

## Current evidence

GitHub main at dacf421 is synchronized locally; migrations 001–020 include the
user-approved migration 011 constraint correction. Hosted
credentials, staging project IDs and an authorized test-media URL have not been
provided for this takeover. No real deployment or end-to-end delivery is claimed.

## Before running a hosted canary

1. Pass GitHub tests and all three container builds.
2. Review and merge the isolated-canary PR before the hosted run. The probe uses
   a transaction-scoped private queue and requires `APP_ENV=staging`; see
   [canary isolation](staging-canary-isolation.md). Confirm rollback on real PGMQ.
3. Verify database JSON/JSONB serialization against real Postgres/PGMQ. This is complete in merged PR #4: main CI run
   [36665893782](https://github.com/franzjemuel/social-saver/actions/runs/36665893782)
   passed migration replay, JSON codecs and the real worker test-job path.
   Hosted Supabase and real media delivery remain unverified.
4. Provision or identify a dedicated staging Telegram bot, Supabase project,
   private R2 bucket and Railway services. Keep provider credentials worker-only.
5. Apply migrations through the explicit staging release gate. Never point this
   gate at a production database or reuse production bot/session credentials.
6. After safe infrastructure checks pass, submit a permitted public Reel, then
   archive-on-save. Verify job status, Telegram delivery and owner-authorized R2
   download. Test cross-tenant denial with a second staging account.
7. Repeat with an authorized Story/Saved Friend. Leave Live and Facebook gated.

Record only non-secret resource identifiers and sanitized outcomes here. Supply
secrets through hosting secret stores, never PR descriptions or committed files.

## Acceptance evidence to record

Deployment commit; migration versions; service health; queue job ID; terminal job
state; confirmed Telegram receipt; archive ownership check; expired download and
cross-tenant denial checks; cleanup outcome. Do not record signed URLs or sessions.
