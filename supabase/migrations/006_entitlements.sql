create table if not exists plans (
  code text primary key,
  name text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists plan_features (
  plan_code text not null references plans(code) on delete cascade,
  feature_key text not null,
  int_value bigint,
  bool_value boolean,
  primary key(plan_code,feature_key),
  check(int_value is not null or bool_value is not null)
);
create table if not exists subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  plan_code text not null references plans(code),
  provider text not null,
  provider_charge_id text,
  status text not null check(status in ('active','canceled','expired','past_due')),
  current_period_start timestamptz,
  current_period_end timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists subscriptions_user_active_idx
  on subscriptions(user_id,current_period_end desc) where status='active';

create table if not exists quota_reservations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  resource_key text not null,
  amount bigint not null check(amount>0),
  job_id uuid references jobs(id) on delete cascade,
  status text not null default 'reserved' check(status in ('reserved','settled','released')),
  actual_amount bigint,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists quota_reservations_active_idx
  on quota_reservations(user_id,resource_key,expires_at) where status='reserved';

insert into plans(code,name) values ('free','Free'),('plus','Plus'),('pro','Pro')
on conflict(code) do nothing;
insert into plan_features(plan_code,feature_key,int_value) values
 ('free','public_downloads_per_day',20),('free','archive_bytes',0),('free','watch_slots',0),('free','live_minutes_monthly',0),
 ('plus','public_downloads_per_day',200),('plus','archive_bytes',10737418240),('plus','watch_slots',5),('plus','live_minutes_monthly',120),
 ('pro','public_downloads_per_day',1000),('pro','archive_bytes',107374182400),('pro','watch_slots',25),('pro','live_minutes_monthly',1000)
on conflict(plan_code,feature_key) do update set int_value=excluded.int_value;
