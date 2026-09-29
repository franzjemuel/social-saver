# Tenant isolation boundary (v2.1)

## Decision

Social Saver uses a backend-only database model. Telegram and the future Lovable / Telegram Mini App UI do **not** query Supabase tables directly. They call the application backend, which authenticates the user and passes the internal `app_user_id` into tenant-scoped repository methods.

This is intentional. The worker needs privileged database access for queues, deduplicated media objects, billing and cross-tenant physical-object reuse. That privileged credential must never be shipped to a browser.

## Archive authorization path

```text
Telegram / future web UI
        |
 authenticated app_user_id
        v
Application backend
        |
        | archive_entry_id + media_asset_id
        v
Repository.get_owned_archive_object()
        |
        | WHERE archive_entry.user_id = app_user_id
        v
R2 object key
        |
        v
short-lived GET presigned URL
```

The storage key is never accepted from a customer as authorization. A user must own the live archive entry and that entry must contain the requested media asset before the backend may mint a download URL.

## v2.0 defect fixed

Several archive repository helpers were accidentally defined at module scope rather than on `Repository`. The bot referenced them as instance methods, so real archive operations could fail at runtime. v2.1 restores them as methods and adds a regression test.

`ArchiveService.delete_entry()` now also treats a non-owned or missing entry as not found rather than attempting to iterate `None`.

## Supabase exposure

Migration `016_tenant_boundary.sql` revokes table, sequence and function privileges in `public` from `anon` and `authenticated`, plus default privileges for later objects created by the migration role. This is defense in depth against accidentally exposing internal tables through the Supabase Data API.

If a future product feature intentionally uses Supabase directly from a browser, create a separate deliberately exposed API schema/table with explicit grants and RLS rather than reopening the internal schema.

## R2 rule

Presigned URLs are bearer credentials. Keep expiry short and mint them only after the ownership query above. Do not expose R2 API credentials to Lovable, Telegram clients, or browser code.

## Lovable boundary

Lovable can build the archive browser, Saved Friends UI, usage dashboard and account screens. It receives customer-safe JSON from our API and a short-lived download URL after authorization. It does not receive the Postgres connection string, Supabase secret/service key, R2 secret key, Instagram session, queue credentials or FFmpeg access.

## Staging gate

Before private beta, create users A and B and prove:

1. A can list and download A's archive item.
2. B cannot list A's archive item.
3. B cannot mint a URL by guessing A's `archive_entry_id` plus `media_asset_id`.
4. B cannot delete A's archive entry.
5. Deleting A's reference does not remove a deduplicated R2 object still referenced by B.
6. `anon` and `authenticated` Data API roles cannot read internal tables.

These are release blockers, not optional tests.
