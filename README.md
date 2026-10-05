# Social Saver

Telegram-first social media saving and private archives. This repository continues
Social Saver v3.8; Git branches and pull requests replace ZIP releases.

**Status: pre-staging.** The imported application has not yet demonstrated a real
Instagram → PGMQ → worker → Telegram/R2 run. Passing offline checks does not prove
hosted services, migrations or Instagram sessions work.

## Development

Use Python 3.12+ and Docker Compose v2:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
./scripts/verify.sh
./scripts/check-package.sh
```

The profile-mirror foundation has a metadata-only local importer. It uses a
database tenant selected by the local operator; it neither downloads video
bytes nor runs in the bot/API path:

```sh
DATABASE_URL=postgresql://... \
SOCIAL_SAVER_ARCHIVE_USER_ID=<existing-app-user-uuid> \
python -m providers.tiktok @aliachin11 --limit 12
```

The development cap is 12 posts. This command is for an authorized local
benchmark import; deterministic tests use fixture metadata and never contact TikTok.

Verification uses blank example configuration for Compose validation, not real
credentials. Container builds require a working Docker engine:

```sh
docker build -t social-saver-bot:local -f Dockerfile .
docker build -t social-saver-worker:local -f Dockerfile.worker .
docker build -t social-saver-api:local -f Dockerfile.api .
```

## Architecture and preserved capabilities

- Bot and authenticated Mini App API enqueue work into Postgres/PGMQ.
- Worker owns modular Instagram extraction, downloads, Telegram delivery, R2
  writes, Saved Friends/autosaving and bounded FFmpeg processing.
- Tenant-scoped archive/job APIs, Telegram Stars billing and entitlements,
  encrypted provider sessions, archive/account deletion and migrations 001–022
  are retained from the supplied archive.
- Live remains gated pending network egress isolation and staging validation.
  Facebook remains a future provider behind the existing interface.

See [architecture](docs/architecture.md), [project state](PROJECT_STATE.md),
[backlog](BACKLOG.md), and [engineering rules](AGENTS.md).

## Staging and release gates

Follow the [current staging handoff](docs/staging-handoff.md) and
[staging launch sequence](docs/staging-launch-v33.md). Configure separate
bot/API/worker secrets following [least privilege](docs/least-privilege-secrets-v38.md).
Use all 22 migrations in order through the controlled staging release script;
never follow historical instructions to apply only migration 001.

No service is deployed merely by importing this repository. Connect staging to a
reviewed commit after CI passes. Production deployment requires explicit approval.
Secrets, provider cookies, session exports and signed URLs must never enter Git,
pull requests, logs or browser storage.

## Workflow

Create one focused branch per improvement. Run verification and all image builds,
update state/backlog, push and open a PR. Keep required CI checks green before
merging. Do not merge risky changes or deploy production autonomously.
See [GitHub workflow](docs/github-workflow.md).

[Bootstrap history](docs/bootstrap-history.md) is historical context, not the
current setup guide. Earlier claims of readiness are not evidence of deployment.
