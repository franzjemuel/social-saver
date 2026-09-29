# Account deletion v3.5

`DELETE /v1/me` is the customer account-deletion boundary. Telegram Mini App authentication identifies the tenant; the browser never supplies a user UUID.

## Sequence

1. API atomically stamps `deletion_requested_at`, pauses all watches/Saved Friends, and soft-deletes all archive entries.
2. API creates a `purge_account` job and sends it through the existing PGMQ queue.
3. Worker re-evaluates physical R2 objects. A deduplicated blob is deleted only when no live archive from any tenant still references it.
4. Worker deletes each orphaned R2 object and marks its storage row deleted.
5. Only after storage cleanup succeeds does the worker delete the `app_users` root. Existing foreign-key cascades remove Telegram identity, watches, jobs, usage/subscription data and other tenant rows.

If R2 deletion fails, the tenant stays logically disabled and the queue retry mechanism retries cleanup. We do not claim physical deletion completed before the worker succeeds.

## Frontend

Lovable may implement Settings > Delete account, a destructive confirmation screen, and the final 202 state. It must not perform database or R2 deletion. The API owns authorization and logical disablement. The worker owns destructive storage cleanup.

## Product/legal boundary

Deletion covers Social Saver's own account data and archived copies. It does not delete the original media from Instagram or another provider. Operational backups, security logs, payment-provider records, and legally required records need explicit retention periods in the public privacy policy before beta.

Telegram exposes a bot privacy-policy URL and can fall back to `/privacy` or Telegram's third-party-app policy when one is not configured. Social Saver should publish its own policy before public beta.
