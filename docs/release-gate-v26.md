# v2.6 release gate

## Problem fixed

The repository had two migrations with version `005`. Supabase migration history requires each migration version to identify one migration. The duplicate made the first real `supabase db push` an avoidable staging risk.

Because staging has not yet been provisioned, v2.6 normalizes the chain before the first remote migration is applied:

* `005_archive_lifecycle.sql`
* `006_entitlements.sql`
* prior `006` through `017` become `007` through `018`

Do not apply the old v2.5 migration directory to a new remote database.

## CI gate

`.github/workflows/ci.yml` runs on every pull request and every push to `main`. It installs the real Python dependencies, compiles the tree, runs the full pytest suite, and separately builds both production Docker images.

In Railway, enable **Wait for CI** on both `social-saver-bot` and `social-saver-worker`. Railway will then skip a deployment when the GitHub workflow fails.

## First staging release order

1. Create the Supabase staging project.
2. Apply the v2.6 migration chain with `scripts/staging-db-release.sh`.
3. Connect both Railway services to the same GitHub repository and `main` branch.
4. Bot service uses `Dockerfile`.
5. Worker service sets `RAILWAY_DOCKERFILE_PATH=Dockerfile.worker`.
6. Add staging secrets from `deploy/staging.env.template` in Railway, never in GitHub.
7. Enable Wait for CI on both services.
8. Deploy worker, then bot.
9. Run `./scripts/staging-canary.sh` from a trusted operator environment with staging credentials.
10. Perform the real Telegram to Instagram to R2 acceptance test from `docs/staging-canary-v2.md`.

## Important migration rule

Once a migration version has been applied to a shared remote environment, never renumber it. Future changes must be additive migrations only. The renumbering in v2.6 is safe only because it occurs before the project's first staging database release.

## Lovable boundary

Lovable is not involved in this release gate. It remains a UI layer for a later archive, Saved Friends, billing, and account dashboard. GitHub Actions, Railway, Supabase migrations, workers, provider sessions, R2, and FFmpeg stay outside Lovable.
