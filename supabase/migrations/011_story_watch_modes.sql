alter table watches add column if not exists content_mode text not null default 'posts'
  check(content_mode in ('posts','stories','both'));

drop index if exists watches_user_id_platform_target_type_target_key_key;
create unique index if not exists watches_user_target_mode_uq
  on watches(user_id,platform,target_type,target_key,content_mode);

alter table watch_seen_items add column if not exists content_kind text not null default 'post'
  check(content_kind in ('post','story'));
alter table watch_deliveries add column if not exists content_kind text not null default 'post'
  check(content_kind in ('post','story'));

alter table watch_seen_items drop constraint if exists watch_seen_items_pkey;
alter table watch_seen_items add primary key(watch_id,content_kind,platform_media_id);

drop index if exists watch_deliveries_watch_id_platform_media_id_key;
create unique index if not exists watch_deliveries_kind_uq
  on watch_deliveries(watch_id,content_kind,platform_media_id);
