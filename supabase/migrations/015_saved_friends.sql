-- Saved Friends: high-frequency Story autosave preferences layered on provider-neutral watches.
alter table watches
  add column if not exists auto_archive boolean not null default false,
  add column if not exists auto_deliver boolean not null default true,
  add column if not exists saved_friend boolean not null default false,
  add column if not exists priority text not null default 'normal'
    check (priority in ('normal','high'));

-- v0.8 originally imposed a five-minute floor. Saved Friends needs a one-minute beta floor.
alter table watches drop constraint if exists watches_poll_interval_seconds_check;
alter table watches add constraint watches_poll_interval_seconds_check
  check (poll_interval_seconds >= 60 and poll_interval_seconds <= 86400);

create index if not exists watches_saved_friends_idx
  on watches(user_id,platform,target_key)
  where saved_friend and status='active';

-- Existing cron dispatch runs once/minute, so a 60-second watch is eligible every cron tick.
