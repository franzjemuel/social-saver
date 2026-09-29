# Staging handoff

## Current evidence

The v3.8 source is available locally with all 20 migrations preserved. Hosted
credentials, staging project IDs and an authorized test-media URL have not been
provided for this takeover. No real deployment or end-to-end delivery is claimed.

## Before running a hosted canary

1. Pass GitHub tests and all three container builds.
2. Review and merge the isolated-canary PR before the hosted run. The probe uses
   a transaction-scoped private queue and requires `APP_ENV=staging`; see
   [canary isolation](staging-canary-isolation.md). Confirm rollback on real PGMQ.
3. Verify database JSON/JSONB serialization against real Postgres/PGMQ. The current
   pool has no JSON codec initialization even though callers send dictionaries
   and treat returned JSON as dictionaries. Static tests cannot prove this works.
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
