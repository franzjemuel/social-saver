create table if not exists stored_objects (
  id uuid primary key default gen_random_uuid(),
  sha256 text not null unique,
  storage_provider text not null default 'r2',
  storage_key text not null unique,
  size_bytes bigint not null check (size_bytes >= 0),
  content_type text,
  created_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table if not exists archive_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  media_item_id uuid not null references media_items(id) on delete cascade,
  created_at timestamptz not null default now(),
  deleted_at timestamptz,
  unique(user_id, media_item_id)
);

create table if not exists archive_entry_assets (
  archive_entry_id uuid not null references archive_entries(id) on delete cascade,
  media_asset_id uuid not null references media_assets(id) on delete cascade,
  stored_object_id uuid not null references stored_objects(id),
  primary key(archive_entry_id, media_asset_id)
);

create index if not exists archive_entries_user_active_idx
  on archive_entries(user_id, created_at desc) where deleted_at is null;
create index if not exists archive_entry_assets_object_idx
  on archive_entry_assets(stored_object_id);

-- Storage bytes are counted from distinct referenced stored objects per user.
create or replace view user_archive_usage as
select ae.user_id,
       count(distinct aea.stored_object_id) as object_count,
       coalesce(sum(distinct so.size_bytes), 0)::bigint as approximate_bytes
from archive_entries ae
join archive_entry_assets aea on aea.archive_entry_id = ae.id
join stored_objects so on so.id = aea.stored_object_id and so.deleted_at is null
where ae.deleted_at is null
group by ae.user_id;
