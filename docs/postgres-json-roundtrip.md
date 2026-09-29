# Postgres JSON and real worker gate (issue #3)

The original pool left asyncpg's JSON types at their defaults: serialized strings.
Repository writes and worker heartbeat writes pass dictionaries, and consumers
expect message/input/result dictionaries. These contracts cannot work together.

Every physical pool connection now registers text-format json/jsonb codecs using
`json.dumps` and `json.loads`, through the pool's `init` callback. Python scalar
strings remain JSON string values; there is no heuristic that treats arbitrary
strings as already-encoded objects. The isolated canary deliberately passes
serialized JSON, so its SQL explicitly binds text then casts that text to jsonb.
See [asyncpg's conversion documentation](https://magicstack.github.io/asyncpg/current/usage.html#custom-type-conversions).

## Disposable integration test

With Python dependencies installed and Docker running:

```sh
source .venv/bin/activate
./scripts/test-integration.sh
```

The runner creates a unique Compose project with a temporary PostgreSQL 18 database,
PGMQ 1.10.0 and pg_cron. It uses an explicitly fake local database password and a
loopback-only port (25432; override SOCIAL_SAVER_TEST_PORT if occupied), replays
all migration files unchanged, then removes the database container and volumes.
It never reads the application's DATABASE_URL. Tests refuse non-loopback DSNs,
a database name other than social_saver_test, or an already-initialized database.
pg_cron is installed but dispatch scheduling is disabled during tests.

The gate is designed to verify (not yet proven by a passing run):

- The unconfigured driver rejects the actual dictionary heartbeat write.
- json/jsonb values survive every pool connection and a replaced connection.
- Tenant-owned job lookup rejects another owner; job input and dead-letter
  records/messages survive real database and PGMQ round-trips.
- Concurrent isolated canaries leave no persistent probe queues.
- The real worker subprocess claims a test job, writes its heartbeat and JSON
  result, marks it completed and archives its PGMQ message. Re-delivery does not
  repeat or change the completed job.

The worker gets a fake Telegram token and no external credentials, runs outside
the checkout to avoid loading a local .env, and is terminated after the test.
The built-in test job performs no Telegram or Instagram requests.

GitHub CI runs the same script in the `integration` job. Ordinary local pytest
runs explicitly skip these five tests when TEST_DATABASE_URL is absent. Skipped
tests are not integration evidence. This gate replays raw migrations, not the
Supabase CLI release flow, and does not prove hosted Supabase permissions,
Instagram extraction, Telegram delivery, R2 behavior or full staging E2E.

This branch is based on open PR #2. Review/merge #2 first, then retarget this PR
to main and require all three CI jobs (`test`, `containers`, `integration`).

## Executed evidence — 2026-09-29

GitHub run [36576459256](https://github.com/franzjemuel/social-saver/actions/runs/36576459256)
at commit 8d83d57 passed the existing test/wheel and container jobs. The disposable
database built and started, but migration replay failed at 011: PostgreSQL refuses
to drop an index owned by a UNIQUE constraint. Both constraint-index drops in that
migration need correction before fresh replay can complete. No migration was
changed; the repository's immutable-migration instruction requires an explicit
exception before applying the proposed correction. None of the five integration
test bodies executed, so no real worker round-trip success is claimed.

Locally, 99 tests and compilation/offline checks passed; five database tests were
skipped. All three container builds timed out because the Docker daemon did not
respond. The separate installed-wheel check failed because the local disk was
full; the corresponding GitHub check passed. No hosted staging test occurred.

Update: Franz approved the migration exception on 2026-09-29. Both constraint
drops in migration 011 are now corrected. Local verification passed (99 tests,
five database tests skipped); real replay and worker success remain pending CI
on the correction. Earlier failure evidence above describes the pre-fix commit.
