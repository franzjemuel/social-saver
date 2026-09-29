# v3.3 staging launch gate

This increment turns the launch backlog into an executable two-stage gate. Stage A is offline and safe for CI. Stage B touches staging services and must only run after credentials are provisioned.

## Stage A: repository readiness

Run `python scripts/staging-readiness.py` or add `--json` for machine-readable output. It verifies a contiguous migration chain, all three production Dockerfiles, the CI workflow, and the online canary. It prints secret **names only**, never values.

The expected migration floor is now 020. The older v2.4 canary accepted 016, which could incorrectly mark a partially migrated v3.x database ready.

## Service boundary

| Service | Dockerfile | Privileged responsibilities |
|---|---|---|
| bot | `Dockerfile` | Telegram ingress and delivery, database jobs |
| api | `Dockerfile.api` | Mini App auth, tenant-scoped reads/writes, R2 download signing |
| worker | `Dockerfile.worker` | provider sessions, extraction, FFmpeg, queue work, R2 writes |

Use separate R2 credentials: API should receive bucket-scoped Object Read only; worker should receive bucket-scoped Object Read & Write. Cloudflare supports both permission levels on bucket-scoped R2 S3 tokens. Do not give Lovable any R2 token.

## Stage B: hosted staging

1. Create a staging Supabase project and enable PGMQ.
2. `supabase link --project-ref <STAGING_REF>`.
3. Preview with `supabase db push --dry-run`, then apply with `supabase db push`.
4. Create one private R2 bucket. Create separate API read-only and worker read/write credentials scoped only to that bucket.
5. Create Railway services `social-saver-worker`, `social-saver-api`, and `social-saver-bot` from the same GitHub repo. Select the corresponding Dockerfile for each service.
6. Configure `/health` on bot/API. Railway healthchecks are deployment readiness checks, not continuous monitoring, so retain heartbeat/Sentry operational checks.
7. Deploy worker first, then API, then bot. Railway CLI supports `railway up --service <name> --environment staging` when manual deployment is needed.
8. Run `./scripts/staging-canary.sh`. It must report migration count >= 20, a fresh worker heartbeat, a healthy Instagram session, queue round-trip, Telegram identity, and R2 round-trip.
9. Only after the canary passes, send one public Reel through Telegram and observe one job, one worker execution and one delivery. Then repeat with archive enabled.

## Lovable boundary

Lovable can build the Mini App screens and call the FastAPI service. It cannot safely replace the worker, provider session vault, PGMQ consumer, FFmpeg recorder, R2 credential holder, or database migration release process.

## GitHub deployment policy

Connect all three Railway staging services to the persistent GitHub repository only after CI is green. Keep production disconnected until staging has passed Reel, archive, Story and two-account tenant-isolation acceptance tests.
