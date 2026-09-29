# Live reliability v2.2

## Failure being fixed
A Live recording can run for up to two hours. PGMQ only guarantees one consumer within the visibility timeout. If visibility is not extended, the same message can be read by another worker. A container crash can also strand `reserved_seconds` forever.

## Queue lease
The worker extends the PGMQ visibility timeout every `QUEUE_HEARTBEAT_SECONDS` (20 seconds by default) to `QUEUE_LONG_JOB_VISIBILITY_SECONDS` (300 seconds from now). PGMQ documents `set_vt` as resetting visibility to an offset from the current time, so this is a rolling lease rather than a one-time timeout.

Invariant: `heartbeat_seconds < long_job_visibility_seconds / 2`.

## Recording idempotency
`live_sessions.job_id` is unique. A redelivered job cannot create two completed recordings. A PostgreSQL advisory lock remains the beta-wide concurrency gate and is acquired before quota reservation.

## Crash-safe quota
Every reservation now has a durable `live_quota_reservations` row with its original billing month and expiration. Settlement is idempotent. Partial captures settle from persisted `live_segments`. `ops/recover_live_leases.py` reconciles expired reservations after a worker/container crash.

Run recovery every 10 minutes in staging/production. It only reaps an expired reservation when there is no recently heartbeating recording.

## Remaining Live blockers
Do not expose customer Live recording yet. Manifest URLs still need strict provider-issued source validation / SSRF controls, and provider headers/cookies need a safe FFmpeg handoff. Instagram Live discovery also needs real staging validation.

## Lovable boundary
None of this belongs in Lovable. Lovable can show recording state, quota and history. Queue leases, quota settlement, FFmpeg, provider authentication and recovery require the backend/worker/database.
