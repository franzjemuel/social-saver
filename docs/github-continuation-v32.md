# GitHub continuation and staging handoff v3.2

The repository is the durable project state. Future iterations should edit it rather than create disconnected ZIP forks.

Use a private `social-saver` repository, protect `main` behind CI, and keep secrets only in deployment/provider secret stores. Use `feat/<name>` or `fix/<name>` branches. Every change updates PROJECT_STATE.md/BACKLOG.md.

Before deployment validate each service without printing secret values:

```bash
SOCIAL_SAVER_ROLE=bot python scripts/check-staging-env.py
SOCIAL_SAVER_ROLE=api python scripts/check-staging-env.py
SOCIAL_SAVER_ROLE=worker python scripts/check-staging-env.py
```

Staging is ready only when migrations apply cleanly once, health/canary checks pass, a real Reel returns to the initiating Telegram user, archive-on-save creates a private R2 object with an authorized temporary download, and a second Telegram account cannot access the first account's job/archive IDs.

Lovable owns presentation only: Save, progress, Archive, Saved Friends, Usage, Upgrade. It never holds Instagram sessions, FFmpeg controls, queue/database admin credentials, Telegram bot token, or R2 write credentials.
