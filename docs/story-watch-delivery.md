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

Current instagrapi docs expose `story_info`, `story_download`, `story_pk_from_url`, and `user_stories`. Story models
contain `thumbnail_url`, `video_url`, `media_type`, `taken_at`, and user metadata. Anonymous Story fetch is not
guaranteed, so the resolver requires the encrypted authenticated session from v1.2.

Operational boundary: capture only content the service account is legitimately allowed to view. Do not attempt to
bypass private-account access controls. Story capture is best-effort because Instagram endpoints and session health can change.
