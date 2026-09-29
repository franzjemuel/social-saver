# Architecture

```text
Telegram bot ───────┐
                   ├─ authenticated tenant + entitlement checks
Mini App → API ────┘                │
                                   ▼
                       Postgres job records + PGMQ
                                   │
                                   ▼
                         worker / processors
                            │            │
                  providers/Instagram    ├─ Telegram delivery
                            │            └─ private R2 archive
                            ▼                        │
                   normalized media                 ▼
                                          owner-checked API download
```

`apps/bot` is Telegram ingress. `apps/api` validates Telegram initData and maps it
to the internal tenant before customer-owned queries. `core/repository.py` owns
persistence; `core/queue.py` wraps PGMQ. `apps/worker/processors` separates media,
watches, Live finalization and deletion. `providers` contains platform adapters;
Facebook must use those interfaces without creating a second delivery pipeline.

Postgres holds job state, tenant ownership, billing/entitlements, watch cursors,
archive references and encrypted sessions. Private R2 holds archived and overflow
bytes. Shared content-addressed objects require reference-aware deletion.

The API can sign owner-authorized downloads using read-only R2 credentials. The
worker holds R2 write credentials and provider session material. Bot/API must not
receive worker provider secrets. Browsers receive neither storage keys nor raw
provider credentials. Keep Live disabled until source validation and network-level
egress controls are proven. Preserve migrations 001–020; make later schema changes
as new migrations.

These are intended boundaries retained from v3.8, not a claim of a completed
security audit or successful hosted end-to-end test.
