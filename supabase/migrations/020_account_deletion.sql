-- v3.5: durable account-deletion state. The user row remains until the worker
-- finishes physical archive cleanup, then ON DELETE CASCADE removes tenant data.
alter table app_users
  add column if not exists deletion_requested_at timestamptz,
  add column if not exists deletion_started_at timestamptz;

create index if not exists app_users_deletion_pending_idx
  on app_users(deletion_requested_at)
  where deletion_requested_at is not null;
