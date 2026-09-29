# Integration gate v1

This gate exists because syntax compilation did not catch earlier runtime wiring defects.

## Local stack
Use Supabase CLI for the database because the project depends on the `pgmq` extension and migrations. The current
Supabase CLI runs the local stack in Docker-compatible containers. `scripts/dev-up.sh` initializes Supabase when
needed, starts it, and applies every migration with `supabase db reset`.

The bot and worker remain separate application containers. The worker MUST use `Dockerfile.worker` because Live
recording/finalization requires FFmpeg. v1.6's compose file accidentally built the ordinary image for the worker;
v1.7 fixes that.

## Required gates before beta
1. `scripts/verify.sh`: compile, pytest, AST parse, Compose validation.
2. `ops/integration_smoke.py`: queues a real PGMQ `test` job and requires a real worker to complete it.
3. Worker heartbeat must appear in `worker_heartbeats` and `/status` must report at least one healthy worker.
4. Controlled HLS test: use `ops/test_live_recorder.py` against a stream you are authorized to record. Verify
   segments land in R2 and finalization is dispatched.
5. Telegram sandbox: test `/start`, link ingestion, `/watch`, `/watchstories`, `/plan`, Stars test payment, and
   cancellation before production Stars are accepted.

## Failure policy
No Instagram or Facebook provider change is allowed to bypass these gates. Provider-specific tests may fail or be
quarantined when upstream behavior changes, but queue, billing, storage, and tenant-isolation tests remain blocking.

## Lovable boundary
Lovable can be used for the future admin/customer dashboard and can call authenticated APIs. It is not the local
integration environment and should not run PGMQ workers, FFmpeg, Instagram sessions, R2 credentials, or long-running
recorders.
