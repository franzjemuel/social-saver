# v3.8 least-privilege deployment gate

The staging environment checker now validates both **missing** secrets and **excessive high-risk** secrets. Run this inside each Railway service after variables are configured:

```bash
SOCIAL_SAVER_ROLE=bot python scripts/check-staging-env.py --strict
SOCIAL_SAVER_ROLE=api python scripts/check-staging-env.py --strict
SOCIAL_SAVER_ROLE=worker python scripts/check-staging-env.py --strict
```

## Contract

| Service | Needs | Must not receive |
|---|---|---|
| bot | Postgres, Telegram | R2 credentials, Instagram credentials, session master key |
| API | Postgres, Telegram, read/presign-scoped R2 credentials | Instagram credentials, session master key |
| worker | Postgres, Telegram, read/write/delete R2, session master key, Instagram service account | n/a |

The script checks **names/presence only** and never prints values. `--strict` returns exit code 3 when a service has a high-risk secret outside its role.

This is intentionally separate from ordinary optional observability credentials. Sentry and Upstash may be present where needed without being treated as provider/storage authority.

## Lovable boundary

Lovable receives none of these infrastructure credentials. It gets only the public API origin and Telegram Mini App runtime data. Telegram `initData` is sent to the API for server-side validation.

## Railway staging acceptance

1. Configure each service independently. Do not use a Railway shared variable group containing every secret.
2. Run the strict check in each service shell.
3. Bot must fail if any R2/provider secret is injected.
4. API must fail if Instagram/session-vault credentials are injected.
5. Worker must have the complete provider/storage set.
6. Only after all three pass, run `./scripts/staging-canary.sh` and the real Reel canary.

## Why this is launch-critical

A compromise of the Telegram bot should not expose archived media storage or Instagram service credentials. A compromise of the browser-facing API should not expose the Instagram service account. The worker is the intentionally privileged boundary and should remain private/non-HTTP.
