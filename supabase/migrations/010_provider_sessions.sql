create table if not exists provider_sessions (
  id uuid primary key default gen_random_uuid(),
  platform text not null,
  account_label text not null,
  encrypted_settings bytea,
  status text not null default 'needs_login'
    check(status in ('needs_login','healthy','challenge','cooldown','disabled')),
  last_validated_at timestamptz,
  cooldown_until timestamptz,
  consecutive_failures integer not null default 0,
  last_error_code text,
  last_error_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(platform,account_label)
);
create index if not exists provider_sessions_usable_idx
  on provider_sessions(platform,status,cooldown_until);

create table if not exists provider_session_events (
  id bigint generated always as identity primary key,
  session_id uuid not null references provider_sessions(id) on delete cascade,
  event_type text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
