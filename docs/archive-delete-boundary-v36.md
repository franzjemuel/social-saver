# v3.6 Archive deletion boundary

## Why this increment exists

v3.4 established a least-privilege rule: user-facing services may logically delete an archive entry, but only the worker may physically delete R2 objects after rechecking deduplicated references. The Mini App API followed that rule, while the Telegram callback still deleted R2 objects directly. v3.6 closes that security regression.

## Authoritative flow

```text
Telegram bot or Mini App
  -> tenant-scoped soft delete in Postgres
  -> create purge_archive job
  -> PGMQ
  -> worker
  -> recheck all live archive references
  -> delete R2 object only if unreferenced
```

## Credential boundary

* Lovable: no R2 credentials.
* FastAPI: read/presign credentials only when archive downloads are enabled.
* Telegram bot: read/presign credentials only for download links. It must not require object-delete permission.
* Worker: read/write/delete R2 credentials.

This keeps destructive storage authority out of both internet-facing customer surfaces.

## Staging acceptance

1. Archive one media object and delete it from Telegram. The archive disappears immediately and a `purge_archive` job is created.
2. Confirm the worker completes the purge and the object is marked deleted when no live archive references remain.
3. Archive the same object for two test users. Delete user A's archive and confirm the physical object remains available to user B.
4. Delete user B's final reference and confirm the worker removes the object.
5. Repeat deletion from the Mini App and confirm identical behavior.
6. Remove R2 delete permission from bot/API credentials and repeat tests 1 and 5. Both user flows must still work.

## Next action

Deploy staging with separate R2 credentials by service, then run the full Instagram Reel canary before adding another provider.
