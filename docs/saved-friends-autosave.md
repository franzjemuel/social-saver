# Saved Friends autosave v1

## Customer behavior
`/savefriend @username` creates a high-priority Instagram Story watch. The first successful poll establishes a
baseline and does not backfill existing Stories. Starting with the next poll, every unseen Story ID is atomically
recorded, queued for media resolution, delivered to Telegram, and archived to R2. `/friends` lists Saved Friends and
`/removefriend @username` removes one.

The beta interval is 60 seconds. This is deliberately polling-based. A notification-triggered fast path can later
call the same `poll_watch` job immediately, but notifications must never be the only detector because delivery can be
late or absent.

## Reliability semantics
* Story identity is `(watch_id, content_kind, platform_media_id)` so repeat polls do not redeliver the same Story.
* Discovery and downloading remain separate queue jobs.
* A Saved Friend is just a provider-neutral watch with `saved_friend=true`; Facebook can implement the same contract.
* Autosave passes `archive=true` into the existing resolver, so the same deduplicated R2/archive path is reused.
* The first poll is baseline-only to avoid unexpectedly archiving a person's entire currently-visible Story tray.
* A provider miss/error never means the user is offline and never deletes prior captures.

## Current Instagram risk
The official Instagram APIs do not provide a general consumer endpoint for watching arbitrary friends' Stories.
This feature therefore depends on an authenticated, unofficial provider adapter. Instagrapi remains actively updated
in 2026 and supports session persistence and Story operations, but its own project documentation warns that private
API automation is fragile in production. Treat this adapter as replaceable and never collect customer Instagram
passwords through Telegram or Lovable.

## Notification fast path, later
Add `provider_events` with a unique provider event ID. A trusted notification adapter can map an observed Story
notification to `target_key` and enqueue `poll_watch` immediately. The 60-second scheduler remains the safety net.
Do not automate around challenges or platform access controls.

## Lovable boundary
Lovable can render Saved Friends, toggles, last capture time, errors, storage usage, and add/remove controls through
an authenticated API. It should not hold Instagram sessions or perform polling/downloads. Supabase Cron can schedule
work, but provider network calls belong in the Python worker.
