# Isolated staging canary

The inherited canary sent a non-UUID marker into the application's `media_jobs`
queue and claimed up to ten messages. A live worker could consume the marker and
fail its UUID lookup, while the probe could hide real jobs for their visibility
timeout. A successful smoke test therefore risked disrupting the system it tested.

The probe now requires `APP_ENV=staging`, creates a random `canary_` queue, checks
send/read/archive, and rolls back its entire database transaction. Its queue,
messages, archive and metadata do not persist. It never reads the configured
application queue. Unique R2 probe keys prevent overlapping runs from overwriting
one another; cleanup runs even when upload/download fails and cleanup errors fail
the check. Errors expose exception classes only.

The database role used for this operator check needs queue-creation permission.
Failure must stop the canary; never fall back to the shared media queue. PGMQ's
[queue functions](https://pgmq.github.io/pgmq/api/sql/functions/) define the SQL
interface used here. The schema/migration state is unchanged by this PR.

Run only against staging with a dedicated staging R2 bucket:

```sh
APP_ENV=staging ./scripts/staging-canary.sh --json
```

`APP_ENV` is an explicit operator guard, not proof that a supplied DSN or bucket
belongs to staging. Independently verify those resource identifiers first.

Unit tests cover isolated queue names, concurrent probes, success/failure/cancel
rollback, string/dict JSON responses, failed archive, R2 cleanup and production
refusal before network calls. Hosted transaction behavior and the real worker
flow still require staging acceptance. A killed process during R2 upload can leave
a `canary/` object; a staging-only expiry rule on that prefix is a useful backstop.
