create table if not exists unlimited_access_overrides (
  user_id uuid primary key references app_users(id) on delete cascade,
  granted_at timestamptz not null default now(),
  note text
);
