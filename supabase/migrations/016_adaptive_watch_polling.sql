-- Adaptive Saved Friends polling.
-- Fast after new Stories, warm after recent activity, and idle backoff for quiet accounts.
alter table watches
  add column if not exists polling_state text not null default 'idle'
    check (polling_state in ('fast','warm','idle')),
  add column if not exists idle_poll_count integer not null default 0,
  add column if not exists last_new_item_at timestamptz;

create index if not exists watches_polling_state_idx
  on watches(polling_state,next_poll_at)
  where saved_friend and status='active';

-- Existing Saved Friends begin idle. The worker promotes them as soon as new content appears.
update watches set polling_state='idle'
where saved_friend and polling_state is null;
