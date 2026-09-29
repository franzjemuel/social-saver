alter table media_assets
  add column if not exists size_bytes bigint,
  add column if not exists sha256 text,
  add column if not exists storage_provider text,
  add column if not exists storage_key text,
  add column if not exists archived_at timestamptz;

create index if not exists media_assets_sha256_idx on media_assets(sha256);
create unique index if not exists media_assets_storage_key_uq
  on media_assets(storage_provider, storage_key)
  where storage_key is not null;
