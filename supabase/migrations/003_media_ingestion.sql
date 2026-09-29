alter table jobs add column if not exists input jsonb not null default '{}'::jsonb;

create table if not exists media_items (
  id uuid primary key default gen_random_uuid(),
  platform text not null,
  platform_media_id text not null,
  canonical_url text not null,
  creator_username text,
  media_type text not null,
  caption text,
  published_at timestamptz,
  resolver_strategy text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(platform, platform_media_id)
);

create table if not exists media_assets (
  id uuid primary key default gen_random_uuid(),
  media_item_id uuid not null references media_items(id) on delete cascade,
  position integer not null,
  asset_type text not null check(asset_type in ('photo','video')),
  source_url text not null,
  width integer,
  height integer,
  duration_seconds double precision,
  created_at timestamptz not null default now(),
  unique(media_item_id, position)
);
