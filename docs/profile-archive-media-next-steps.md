# Profile archive media next steps

## Bounded video batches

Add an explicit profile-media batch request only after the single-video canary
passes. It should carry a bounded maximum, persist per-profile progress, enqueue
independent targeted jobs, and report completed/failed/skipped counts. A retry
must resume unpersisted assets only, respect provider concurrency limits, and
stop after a finite retry ceiling. Batch scheduling must never turn a user action
into an unbounded profile crawl.

## Photos and carousels

Keep each ordered source item as an `archived_post_media_assets` row. Add a
worker-owned image acquisition path that validates image bytes and dimensions,
deduplicates through `stored_objects`, and attaches each asset by position. A
carousel viewer can then render the ordered sequence without inventing a video.
Optional slideshow audio should use an explicit asset role rather than being
attached to an arbitrary image.

## Profile rescans and removals

A later scan should upsert known posts, append engagement snapshots with their
observation timestamps, create newly discovered posts, and mark previously seen
but absent posts as `is_present_on_original=false`. It must never delete an
archived post or stored object merely because an upstream profile no longer
returns it. The read API can continue to label such content as preserved.

## Historical engagement

The existing snapshots retain observed counts and timestamps. Future UI should
show them as archived observations, not live values, and select the latest
snapshot only for summary views while retaining the full history for charts.
