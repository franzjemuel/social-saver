-- Provider-neutral, tenant-private profile mirrors. Existing media_items remain
-- the save/download pipeline and are deliberately not repurposed here.
create table if not exists archived_profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  platform text not null,
  platform_account_id text not null,
  username text not null,
  display_name text,
  bio text,
  avatar_url text,
  metadata jsonb not null default '{}'::jsonb,
  first_archived_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  unique(user_id, platform, platform_account_id)
);

create table if not exists archived_posts (
  id uuid primary key default gen_random_uuid(),
  archived_profile_id uuid not null references archived_profiles(id) on delete cascade,
  platform_post_id text not null,
  original_url text not null,
  media_type text not null,
  caption text,
  published_at timestamptz,
  thumbnail_url text,
  is_present_on_original boolean not null default true,
  first_archived_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  unique(archived_profile_id, platform_post_id)
);

create table if not exists archived_post_media_assets (
  id uuid primary key default gen_random_uuid(),
  archived_post_id uuid not null references archived_posts(id) on delete cascade,
  position integer not null,
  asset_type text not null,
  source_url text,
  thumbnail_url text,
  duration_seconds double precision,
  width integer,
  height integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(archived_post_id, position)
);

create table if not exists archived_post_engagement_snapshots (
  id uuid primary key default gen_random_uuid(),
  archived_post_id uuid not null references archived_posts(id) on delete cascade,
  observed_at timestamptz not null,
  view_count bigint,
  like_count bigint,
  comment_count bigint,
  repost_count bigint,
  share_count bigint,
  save_count bigint,
  metadata jsonb not null default '{}'::jsonb,
  unique(archived_post_id, observed_at)
);

create index if not exists archived_profiles_user_idx
  on archived_profiles(user_id, last_observed_at desc);
create index if not exists archived_posts_profile_idx
  on archived_posts(archived_profile_id, published_at desc);
