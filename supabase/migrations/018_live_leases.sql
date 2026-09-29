-- v2.2: crash-safe Live quota reservations and idempotent job/session ownership.
alter table live_sessions add column if not exists job_id uuid references jobs(id) on delete set null;
create unique index if not exists live_sessions_job_id_uidx on live_sessions(job_id) where job_id is not null;

create table if not exists live_quota_reservations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  month_start date not null,
  live_session_id uuid references live_sessions(id) on delete set null,
  reserved_seconds integer not null check (reserved_seconds > 0),
  actual_seconds integer,
  status text not null default 'reserved' check(status in ('reserved','settled','released')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  settled_at timestamptz
);
create index if not exists live_quota_reservations_reap_idx
  on live_quota_reservations(expires_at) where status='reserved';
create unique index if not exists live_quota_reservations_session_uidx
  on live_quota_reservations(live_session_id) where live_session_id is not null and status='reserved';
