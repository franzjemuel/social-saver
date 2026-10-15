-- One-time, staging-only authorization for a manually approved Highlight
-- canary. This is deliberately separate from a job budget: an authorization
-- binds one owned video Story to one job and cannot be recreated by queue
-- redelivery or browser input.
create table if not exists highlight_provider_canary_authorizations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users(id) on delete cascade,
  archived_profile_id uuid not null references archived_profiles(id) on delete cascade,
  archived_highlight_story_membership_id uuid not null
    references archived_highlight_story_memberships(id) on delete cascade,
  provider_namespace text not null check (length(provider_namespace) between 1 and 128),
  authorization_reference text not null check (length(authorization_reference) between 1 and 128),
  budget_micro_usd bigint not null check (budget_micro_usd > 0 and budget_micro_usd <= 650000),
  status text not null check (status in ('authorized','claimed','consumed','failed_known','indeterminate','revoked')),
  claimed_job_id uuid references jobs(id) on delete set null,
  claimed_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  unique(authorization_reference)
);

create unique index if not exists highlight_provider_canary_one_active_target_idx
  on highlight_provider_canary_authorizations(
    user_id, archived_profile_id, archived_highlight_story_membership_id, provider_namespace
  ) where status in ('authorized','claimed');

create index if not exists highlight_provider_canary_job_idx
  on highlight_provider_canary_authorizations(claimed_job_id)
  where claimed_job_id is not null;

-- A run ID is a durable non-secret receipt. Persist it immediately after an
-- Actor acknowledges the run so a crash can never make the worker start a
-- replacement paid run. A later operator phase may reconcile a dispatched run
-- by its receipt; ordinary redelivery never does so automatically.
alter table highlight_provider_invocations
  drop constraint if exists highlight_provider_invocations_status_check;

alter table highlight_provider_invocations
  add constraint highlight_provider_invocations_status_check
  check (status in ('reserved','started','dispatched','succeeded','failed_known','indeterminate','settled'));

alter table highlight_provider_invocations
  add column if not exists execution_key text
    check (execution_key is null or length(execution_key) between 1 and 128);

create unique index if not exists highlight_provider_invocations_execution_key_idx
  on highlight_provider_invocations(job_id, execution_key)
  where execution_key is not null;
