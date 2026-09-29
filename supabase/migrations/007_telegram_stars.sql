alter table plans add column if not exists stars_price integer;
update plans set stars_price=299 where code='plus' and stars_price is null;
update plans set stars_price=799 where code='pro' and stars_price is null;

create table if not exists payment_events (
  id uuid primary key default gen_random_uuid(),
  telegram_payment_charge_id text not null unique,
  user_id uuid not null references app_users(id) on delete cascade,
  plan_code text not null references plans(code),
  invoice_payload text not null,
  currency text not null,
  total_amount bigint not null,
  is_recurring boolean not null default false,
  is_first_recurring boolean not null default false,
  subscription_expiration_date timestamptz,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists payment_events_user_idx on payment_events(user_id,created_at desc);
