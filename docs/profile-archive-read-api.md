# Profile archive read API

All routes require verified Telegram Mini App initData and are tenant-scoped by
the server-derived application user. They never accept a browser-supplied user
identifier.

- `GET /v1/profile-archives?limit=25&offset=0` lists private archived profile
  summaries and post counts.
- `GET /v1/profile-archives/{profile_id}` returns one owned profile summary.
- `GET /v1/profile-archives/{profile_id}/posts?limit=30&offset=0` returns
  newest-first posts and each post's latest engagement snapshot, or `null`.

Profile metadata, TikTok `secUid`, source acquisition URLs, storage keys, and
provider/session data are intentionally excluded. Missing and foreign profile
IDs both return `404 profile_archive_not_found`. A frontend can render the
preserved-upstream-removal state from `is_present_on_original: false`.
