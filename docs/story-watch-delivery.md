# Instagram Story watch delivery

v1.3 closes the Story path:
authenticated `user_stories()` discovery -> canonical Story URL -> `InstagramStoryProvider` -> standard
`ResolvedMedia` -> existing download, Telegram delivery, overflow R2, and optional archive infrastructure.

Commands:
* `/watch @user` watches posts/Reels only, preserving prior behavior.
* `/watchstories @user` watches Stories only.
* `/watchboth @user` watches both.

Existing watches remain posts-only after migration. This avoids silently expanding collection scope.

The first Story poll is baseline-only. Later unseen Story IDs are queued automatically. Story IDs are deduplicated
separately from post IDs.

Public Story watches use the worker-only Apify provider in `onlyNew` monitoring mode.
The first poll establishes a baseline. Later rows are deduplicated in `watch_seen_items`,
stored as delivery jobs with their temporary media URL, and fanned out to every active
tenant watch for the same public profile. Direct private Story links still use the
authenticated session provider; public monitoring does not receive Instagram credentials.
Normal public Story watches poll every five minutes. Saved Friends retain the adaptive
one-minute/three-minute/fifteen-minute cadence and automatic archive behavior.

Operational boundary: capture only content the service account is legitimately allowed to view. Do not attempt to
bypass private-account access controls. Story capture is best-effort because Instagram endpoints and session health can change.
