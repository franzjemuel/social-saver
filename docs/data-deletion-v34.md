# v3.4 Archive deletion and retention boundary

The Mini App can now delete an archive entry with `DELETE /v1/archive/{entry_id}`. The API tenant-checks and soft-deletes the ownership row immediately, then queues `purge_archive`. Only the worker has R2 write/delete credentials.

The cleanup worker rechecks physical-object references at execution time. This is required because archive blobs are content-addressed and may be shared by multiple tenants. A user's deletion must never remove bytes still referenced by another live archive entry.

## Lovable

Lovable owns the confirmation dialog, optimistic removal from the Archive list, and polling the returned job ID. It must not receive R2 delete credentials or issue object-store operations.

## Backend

FastAPI: authorization, soft delete, enqueue cleanup. Postgres: ownership and reference truth. Worker: recheck references and delete orphaned R2 objects. R2: private physical storage.

## Retention policy for beta

User-requested archive deletion is immediate at the product layer and asynchronous at the physical layer. Do not enable an R2 bucket lock on customer archive prefixes because a lock can override lifecycle deletion and conflict with deletion commitments. Keep incomplete multipart-upload cleanup enabled. A later account-deletion workflow should reuse the same queue-based purge primitive.

## Acceptance tests

1. Tenant A cannot delete Tenant B's entry and receives the same 404 as a missing entry.
2. Deleted entry disappears immediately from A's archive.
3. Unique blob is removed by worker and `stored_objects.deleted_at` is set.
4. Deduplicated blob referenced by Tenant B remains in R2.
5. Repeated DELETE returns 404 and cannot create an additional purge.
6. API service works with read-only R2 credentials because it never deletes objects directly.
