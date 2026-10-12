-- Provider-neutral, tenant-private Highlight metadata. These records model
-- collection membership only; they deliberately do not retain media URLs,
-- signed URLs, provider responses, or storage attachments.
create table if not exists archived_highlight_collections (
  id uuid primary key default gen_random_uuid(),
  archived_profile_id uuid not null references archived_profiles(id) on delete cascade,
  provider_namespace text not null check (length(provider_namespace) between 1 and 128),
  provider_collection_id text not null check (length(provider_collection_id) between 1 and 512),
  title text,
  reported_item_count integer check (reported_item_count is null or reported_item_count >= 0),
  has_cover_descriptor boolean not null default false,
  last_snapshot_complete boolean not null default false,
  first_archived_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  last_complete_observed_at timestamptz,
  last_absent_at timestamptz,
  is_present_on_original boolean not null default true,
  unique(archived_profile_id, provider_namespace, provider_collection_id),
  unique(id, provider_namespace)
);

create table if not exists archived_highlight_story_memberships (
  id uuid primary key default gen_random_uuid(),
  archived_highlight_collection_id uuid not null,
  provider_namespace text not null check (length(provider_namespace) between 1 and 128),
  provider_item_id text not null check (length(provider_item_id) between 1 and 512),
  provider_position integer not null check (provider_position >= 0),
  media_type text not null check (length(media_type) between 1 and 64),
  caption text,
  published_at timestamptz,
  duration_seconds double precision check (duration_seconds is null or duration_seconds >= 0),
  has_media_descriptor boolean not null default false,
  has_cover_descriptor boolean not null default false,
  first_archived_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  last_absent_at timestamptz,
  is_present_on_original boolean not null default true,
  foreign key (archived_highlight_collection_id, provider_namespace)
    references archived_highlight_collections(id, provider_namespace)
    on delete cascade,
  unique(archived_highlight_collection_id, provider_namespace, provider_item_id)
);

create index if not exists archived_highlight_collections_profile_idx
  on archived_highlight_collections(archived_profile_id, last_observed_at desc);

create index if not exists archived_highlight_memberships_collection_position_idx
  on archived_highlight_story_memberships(
    archived_highlight_collection_id, provider_position, first_archived_at, id
  );
