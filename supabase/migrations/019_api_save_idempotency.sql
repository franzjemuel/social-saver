-- Mini App/API save requests use the same jobs table and worker queue as Telegram.
-- client_request_id prevents duplicate jobs from double taps or HTTP retries.
alter table jobs add column if not exists source_channel text not null default 'telegram'
  check (source_channel in ('telegram','mini_app','system'));
alter table jobs add column if not exists client_request_id text;
create unique index if not exists jobs_user_client_request_uidx
  on jobs(user_id, client_request_id) where client_request_id is not null;
