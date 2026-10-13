-- Durable reservation ledger for future paid Highlight-provider calls.
-- It is intentionally independent from Actor credentials and does not enable a
-- provider. Amounts use integer micro-USD to avoid floating-point budgets.
create table if not exists highlight_provider_job_budgets (
  job_id uuid primary key references jobs(id) on delete cascade,
  user_id uuid not null references app_users(id) on delete cascade,
  archived_profile_id uuid not null references archived_profiles(id) on delete cascade,
  budget_micro_usd bigint not null check (budget_micro_usd > 0),
  reserved_micro_usd bigint not null default 0 check (reserved_micro_usd >= 0),
  settled_micro_usd bigint check (settled_micro_usd is null or settled_micro_usd >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (reserved_micro_usd <= budget_micro_usd)
);

create table if not exists highlight_provider_invocations (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  provider_namespace text not null check (length(provider_namespace) between 1 and 128),
  operation text not null check (operation in ('collection_list', 'collection_items')),
  sequence integer not null check (sequence >= 0),
  reserved_micro_usd bigint not null check (reserved_micro_usd > 0),
  status text not null check (status in ('reserved','started','succeeded','failed_known','indeterminate','settled')),
  provider_run_receipt text check (provider_run_receipt is null or length(provider_run_receipt) <= 512),
  error_code text check (error_code is null or length(error_code) <= 128),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  unique(job_id, sequence)
);

create index if not exists highlight_provider_invocations_job_idx
  on highlight_provider_invocations(job_id, sequence);
