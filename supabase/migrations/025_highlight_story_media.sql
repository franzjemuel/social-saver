-- Private, persisted media for tenant-owned Highlight Story memberships.
-- Acquisition descriptors deliberately never enter this schema: a worker must
-- resolve them in memory for each explicitly authorized attempt.
create table if not exists archived_highlight_story_media_assets (
  id uuid primary key default gen_random_uuid(),
  archived_highlight_story_membership_id uuid not null
    references archived_highlight_story_memberships(id) on delete cascade,
  position integer not null check (position >= 0),
  asset_type text not null check (asset_type in ('video', 'photo')),
  stored_object_id uuid references stored_objects(id),
  duration_seconds double precision check (duration_seconds is null or duration_seconds >= 0),
  width integer check (width is null or width > 0),
  height integer check (height is null or height > 0),
  archive_attempt_count integer not null default 0 check (archive_attempt_count >= 0),
  last_archive_attempt_at timestamptz,
  last_archived_at timestamptz,
  last_failure_code text,
  first_archived_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  unique(archived_highlight_story_membership_id, position)
);

create index if not exists archived_highlight_story_media_assets_object_idx
  on archived_highlight_story_media_assets(stored_object_id)
  where stored_object_id is not null;

create index if not exists archived_highlight_story_media_assets_membership_idx
  on archived_highlight_story_media_assets(archived_highlight_story_membership_id, position);
