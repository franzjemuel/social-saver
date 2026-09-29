create table if not exists worker_heartbeats (
  worker_id text primary key,
  service text not null default 'media-worker',
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

alter table live_sessions
  add column if not exists final_storage_key text,
  add column if not exists final_size_bytes bigint,
  add column if not exists finalized_at timestamptz,
  add column if not exists finalize_status text not null default 'pending',
  add column if not exists finalize_error text;
