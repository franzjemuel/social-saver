# Staging canary v2.4

This is the release gate between "the repository builds" and "real beta traffic is allowed".

## What it proves

`python -m ops.staging_canary` checks, without printing secrets:

1. Telegram bot token is accepted by `getMe`.
2. Postgres accepts a connection.
3. PGMQ is installed.
4. At least 16 schema migrations are recorded.
5. The media queue can send, claim, and archive a namespaced canary message.
6. A worker heartbeat is no older than 90 seconds.
7. The Instagram provider session is persisted as healthy and has been validated.
8. R2 can put, retrieve, verify, and delete a temporary object.

Any failure exits nonzero. Do not invite beta users until the command reports `READY`.

## Staging sequence

```bash
# 1. Apply migrations only after dry-run review
./scripts/staging-db-release.sh

# 2. Deploy worker first and wait for heartbeat
# 3. Run infrastructure canary from the worker image/environment
python -m ops.staging_canary

# 4. Deploy bot
# 5. Run the real media canary below from Franz's Telegram account
```

## Real media canary

Infrastructure readiness is necessary but not sufficient. Before beta, use a public Instagram post owned by or authorized for the tester and prove this chain manually:

`Telegram -> bot -> PGMQ -> worker -> Instagram provider -> download -> R2/archive -> Telegram delivery`.

Record the job ID. Verify one completed job, one archive entry, expected object size/hash, and exactly one Telegram delivery. Repeat with a carousel and a public Reel. Then add one authorized Saved Friend and verify a newly posted Story is captured exactly once.

Do not use Live as a launch gate. Customer Live remains disabled until network egress isolation and reliable provider discovery are separately proven.

## Rollback rule

If any canary fails after a deployment, stop new beta invitations, keep existing media immutable, roll the bot/worker image back to the last known-good release, and investigate from job/provider-session events. Never "fix" a failed canary by weakening ownership, session, or source validation.
