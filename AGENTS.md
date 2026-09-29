# Social Saver engineering instructions

Read PROJECT_STATE.md and BACKLOG.md before editing. Pick one highest-value unresolved increment per branch. Add tests, run `./scripts/verify.sh`, then update project state/backlog in the same change.

Never commit credentials, cookies, provider sessions, Telegram tokens, database/R2 secrets, or signed URLs. Browser/Lovable code never receives provider sessions, queue credentials, FFmpeg controls, or privileged storage/database keys. Every customer-owned query must derive tenant identity from authenticated Telegram context, never a browser-supplied internal user ID.

Keep provider extraction behind `providers/`. Bot/API enqueue work; workers own downloads, FFmpeg, provider sessions, and delivery orchestration. Keep Live disabled for customers until network egress isolation and source validation pass. Do not add access-control circumvention.

Launch priority: prove a real Instagram URL through Telegram/API -> Postgres/PGMQ -> worker -> Instagram provider -> Telegram delivery and optional R2 archive before Facebook work.
