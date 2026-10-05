-- Persisted profile media reuses the existing, SHA-256-deduplicated object store.
alter table archived_post_media_assets
  add column if not exists stored_object_id uuid references stored_objects(id);

create index if not exists archived_post_media_assets_object_idx
  on archived_post_media_assets(stored_object_id)
  where stored_object_id is not null;
