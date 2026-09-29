# Backlog

## P0 launch blockers
- [ ] Put current project in persistent GitHub repository.
- [ ] Provision staging Telegram bot/token.
- [ ] Provision Postgres/Supabase + PGMQ.
- [ ] Provision private R2 + least-privilege API/worker credentials.
- [ ] Provision Railway bot/API/worker. Config-as-code is ready in v3.7; external project/service creation remains.
- [ ] Apply migrations and pass staging canary.
- [ ] Complete real Reel -> queue -> worker -> Telegram test.
- [ ] Complete archive-on-save -> R2 -> authorized download test.
- [ ] Complete two-account tenant-isolation acceptance test.

## P1 private beta
- [ ] Mini App job progress UI.
- [ ] Archive UI and Saved Friends management UI.
- [x] Archive deletion and physical cleanup boundary.
- [x] Full account deletion pipeline.
- [ ] Finalize backup/log/payment retention periods and deletion UX copy.
- [ ] Privacy policy and terms.
- [ ] Measure costs and tune quotas/pricing.
- [ ] Operational alerts for queue lag, heartbeat, provider session and delivery failures.

## P2 hardening
- [ ] Recorder network egress isolation and real Live staging proof.
- [ ] Credential/session rotation runbook.
- [ ] Database backup/restore drill.
- [ ] Abuse/takedown/support workflow.

## P3 expansion
- [ ] Facebook provider adapter after Instagram launch gate.
- [ ] Facebook Stories through existing watch/archive contracts.

- [x] Enforce worker-only physical archive deletion from both Mini App and Telegram bot (v3.6).

- [x] Add GitHub-to-Railway config-as-code and build all three service images in CI (v3.7).

- [x] Enforce per-service least-privilege secret boundaries with a strict deployment gate (v3.8).
