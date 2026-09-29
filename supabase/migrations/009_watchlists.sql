create extension if not exists pg_cron;

create table if not exists watches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  platform text not null,
  target_type text not null default 'profile',
  target_key text not null,
  target_display text,
  status text not null default 'active' check(status in ('active','paused','error')),
  cursor jsonb not null default '{}'::jsonb,
  poll_interval_seconds integer not null default 900 check(poll_interval_seconds >= 300),
  next_poll_at timestamptz not null default now(),
  last_polled_at timestamptz,
  consecutive_failures integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  unique(user_id,platform,target_type,target_key)
);
create index if not exists watches_due_idx on watches(next_poll_at) where status='active';

create table if not exists watch_seen_items (
  watch_id uuid not null references watches(id) on delete cascade,
  platform_media_id text not null,
  first_seen_at timestamptz not null default now(),
  primary key(watch_id,platform_media_id)
);

create table if not exists watch_deliveries (
  id bigint generated always as identity primary key,
  watch_id uuid not null references watches(id) on delete cascade,
  platform_media_id text not null,
  job_id uuid references jobs(id) on delete set null,
  created_at timestamptz not null default now(),
  unique(watch_id,platform_media_id)
);

create or replace function dispatch_due_watches(batch_size integer default 25)
returns integer language plpgsql as $$
declare w record; dispatched integer := 0; j uuid;
begin
  for w in
    select * from watches
    where status='active' and next_poll_at <= now()
    order by next_poll_at
    for update skip locked
    limit batch_size
  loop
    insert into jobs(user_id,telegram_chat_id,job_type,input)
      select w.user_id,ta.telegram_user_id,'poll_watch',
             jsonb_build_object('watch_id',w.id)
      from telegram_accounts ta where ta.app_user_id=w.user_id
      order by ta.created_at limit 1
      returning id into j;
    if j is not null then
      perform pgmq.send('media_jobs',jsonb_build_object('version',1,'job_id',j::text),0);
      update watches set next_poll_at=now()+make_interval(secs=>poll_interval_seconds) where id=w.id;
      dispatched := dispatched + 1;
    end if;
  end loop;
  return dispatched;
end $$;

select cron.schedule('social-saver-watch-dispatch','* * * * *',
  $$select dispatch_due_watches(25);$$);
