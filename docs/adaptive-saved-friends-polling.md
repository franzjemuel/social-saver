# Adaptive Saved Friends polling — v2.0

## Goal
Catch new Stories quickly without polling every quiet profile once per minute forever.

## State machine

`idle --new story--> fast --3 quiet polls--> warm --8 total quiet polls--> idle`

| State | Interval | Purpose |
| --- | ---: | --- |
| fast | 60 s | Recent Story activity or newly captured item |
| warm | 180 s | Recently checked, no new item for several polls |
| idle | 900 s | Quiet profile; low request pressure |

A provider/session error immediately backs the watch off to at least 15 minutes, doubling up to one hour on repeated attempts. Existing five-failure quarantine remains in force.

The dispatcher is still provider-neutral. Instagram discovery decides only what media exists. `WatchService` owns scheduling policy. A future Facebook Stories provider therefore inherits the same scheduler without duplicating cadence logic.

## Important beta behavior
A new Saved Friend begins at 60 seconds because `/savefriend` explicitly sets the interval and queues the first poll. First discovery establishes a baseline and does not archive historical visible Stories. A newly captured Story resets the watch to fast. Quiet accounts eventually settle at 15 minutes.

## Notification accelerator later
An Instagram notification listener, if we find a reliable permitted signal, should not download media itself. It should only set `next_poll_at=now()` for the matching Saved Friend. Scheduled polling remains the reliability path.

## Scale intuition
With 20 Saved Friends, fixed 60-second polling can require about 28,800 profile checks/day per user. If all 20 settle to the 15-minute idle tier, that falls to about 1,920/day, a 93.3% reduction before accounting for temporarily active profiles. This is request-volume arithmetic, not an Instagram-safe-rate claim.

## Lovable boundary
Lovable can display `polling_state`, `last_polled_at`, `last_new_item_at`, and a friendly label such as Active / Watching / Quiet. It must not run provider polling or hold Instagram credentials. The worker, Postgres scheduler, queue, and provider session remain backend infrastructure.

## Beta acceptance tests
1. Add a Saved Friend and confirm first poll establishes baseline without historical delivery.
2. Inject one unseen Story and confirm it queues exactly once and changes state to fast/60s.
3. Complete three quiet polls and confirm warm/180s.
4. Reach eight quiet polls and confirm idle/900s.
5. Cause a provider error and confirm interval backs off to >=900s.
6. Recover and capture a new item; confirm failures reset and cadence returns to 60s.
