alter table subscriptions
  add column if not exists initial_charge_id text,
  add column if not exists cancel_at_period_end boolean not null default false,
  add column if not exists canceled_at timestamptz;

update subscriptions set initial_charge_id=provider_charge_id
where initial_charge_id is null and provider='telegram_stars';

create unique index if not exists subscriptions_initial_charge_uq
  on subscriptions(initial_charge_id) where initial_charge_id is not null;

alter table payment_events
  add column if not exists refunded_at timestamptz,
  add column if not exists refund_reason text;

create table if not exists subscription_audit (
  id bigint generated always as identity primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  subscription_id uuid references subscriptions(id) on delete set null,
  action text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
