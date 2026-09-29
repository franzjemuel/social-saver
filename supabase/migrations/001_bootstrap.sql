create extension if not exists pgcrypto;
create extension if not exists pgmq;

create table if not exists app_users (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now()
);

create table if not exists telegram_accounts (
  telegram_user_id bigint primary key,
  app_user_id uuid not null references app_users(id) on delete cascade,
  username text,
  first_name text,
  created_at timestamptz not null default now()
);

create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  telegram_chat_id bigint not null,
  job_type text not null,
  platform text,
  status text not null default 'queued'
    check (status in ('queued','running','completed','failed')),
  progress smallint not null default 0 check (progress between 0 and 100),
  result jsonb,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz
);

create table if not exists job_attempts (
  id bigint generated always as identity primary key,
  job_id uuid not null references jobs(id) on delete cascade,
  attempt_number integer not null,
  worker_id text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  success boolean,
  error_code text,
  error_message text,
  unique(job_id, attempt_number)
);

create table if not exists usage_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  event_type text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

do $$
begin
  if not exists (select 1 from pgmq.list_queues() where queue_name = 'media_jobs') then
    perform pgmq.create('media_jobs');
  end if;
end $$;
