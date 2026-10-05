# Profile archive video canary

## Staging acceptance result

The single-video staging acceptance completed successfully for the benchmark
archive: one targeted job persisted one video, authorized playback returned a
short-lived available video response that a browser read successfully, and a
same-post retry did not create another attachment or process another post. One
of the 12 benchmark posts now has archived media; the other 11 remain
intentionally unprocessed. This does not validate full-profile batches or
photo/carousel persistence. The frontend playback implementation remains a
separate PR.

Run this only in the existing staging environment, with an authorized Telegram
Mini App session and the normal API/PGMQ/worker path. It is intentionally a
single-post procedure: do not use the bulk archive-media route or process a
second benchmark post while validating this increment.

1. Confirm the deployed worker image is the reviewed PR commit, its heartbeat is
   fresh, and staging is at migration `023`.
2. Confirm the worker's least-privilege R2 configuration with a bounded temporary
   write/head/read/delete probe. Do not retain the probe object.
3. Resolve the benchmark archive through the authenticated tenant boundary,
   verify there are 12 imported posts, and record the existing persisted-video
   count before selecting the one target.
4. Select one unpersisted `video` post with a video media-asset row. Reuse the
   previously selected canary post when retrying.
5. Call the single-post archive-media endpoint through the normal authenticated
   Mini App request. It must create one targeted job and one PGMQ message.
6. Observe the job to completion. Require `attached=1`, `failures=0`, and exactly
   one of `uploaded=1` or `reused=1`.
7. Verify exactly one archived profile post now has persisted media. Do not expose
   object IDs, storage keys, provider URLs, or hashes while checking this.
8. Request playback for that same owned post. Fetch the short-lived signed result
   without logging or retaining it, and require a successful non-empty video
   response with a valid MP4/container signature rather than HTML or JSON.
9. Reissue the same targeted request only to prove idempotency. It must not create
   a second media attachment, stored object, or benchmark-post acquisition.
10. Record only sanitized job counts and outcome categories. Keep the remaining
    benchmark posts intentionally unprocessed.

Stop on the first failure. Preserve tenant isolation, do not add cookies or a
provider login, and do not compensate by manually attaching database rows or
writing objects directly to R2.
