create table if not exists live_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  platform text not null,
  target_key text not null,
  source_id text,
  status text not null default 'queued'
    check(status in ('queued','recording','completed','failed','stopped','quota_exhausted')),
  started_at timestamptz,
  ended_at timestamptz,
  heartbeat_at timestamptz,
  max_minutes integer not null,
  recorded_seconds integer not null default 0,
  bytes_uploaded bigint not null default 0,
  stop_reason text,
  error_code text,
  created_at timestamptz not null default now()
);
create index if not exists live_sessions_active_idx
  on live_sessions(status,heartbeat_at) where status in ('queued','recording');

create table if not exists live_segments (
  id bigint generated always as identity primary key,
  live_session_id uuid not null references live_sessions(id) on delete cascade,
  segment_index integer not null,
  storage_key text not null,
  size_bytes bigint not null,
  duration_seconds integer not null,
  created_at timestamptz not null default now(),
  unique(live_session_id,segment_index)
);

create table if not exists live_usage_monthly (
  user_id uuid not null references app_users(id) on delete cascade,
  month_start date not null,
  used_seconds integer not null default 0,
  reserved_seconds integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key(user_id,month_start)
);
