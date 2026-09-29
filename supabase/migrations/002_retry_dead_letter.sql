-- Retry/dead-letter increment
-- read_ct on PGMQ messages is the authoritative delivery-attempt count.

do $$
begin
  if not exists (select 1 from pgmq.list_queues() where queue_name = 'dead_letter_jobs') then
    perform pgmq.create('dead_letter_jobs');
  end if;
end $$;

create table if not exists dead_letter_events (
  id bigint generated always as identity primary key,
  job_id uuid references jobs(id) on delete set null,
  source_queue text not null,
  source_msg_id bigint not null,
  read_count bigint not null,
  error_code text not null,
  error_message text,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  unique(source_queue, source_msg_id)
);
