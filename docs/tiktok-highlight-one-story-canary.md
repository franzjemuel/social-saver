# TikTok Highlight one-Story staging acceptance

This runbook is intentionally inactive until provider licensing, migrations
025 through 027, durable per-invocation budget accounting, and an explicit
staging authorization have all been approved. It never authorizes production
use.

## Required merge and rollout order

1. Merge the Highlight Story media foundation (PR #38) before its durable
   provider-invocation ledger (PR #39), then merge the hardened MP4 transport
   (PR #40), and finally the descriptor-to-archive bridge built on that stack.
   Each PR is reviewed against the immediately preceding main commit; do not
   squash a later stack member into main before its dependency is present.
2. Apply immutable migration 025 before any Highlight media job can be queued.
   It creates only metadata/attachment rows and contains no descriptor URL.
3. Apply immutable migration 026 before any approved external provider
   invocation. It supplies the budget/invocation ledger used to make ambiguous
   external outcomes terminal rather than automatically repeatable.
4. Apply immutable migration 027 before the canary. It binds exactly one owned
   video Story to one operator-created authorization and adds the durable
   asynchronous Actor-run `dispatched` receipt state.
5. Keep all Highlight feature gates off through these merges and migrations.
   The bridge remains unregistered until a separate provider-license and
   spending authorization enables the reviewed worker factory.

## Preconditions

1. Staging API and worker run the reviewed feature commit; migrations 025,
   026, and 027 are applied through the normal controlled release procedure and their
   schema head is verified before the feature gates change.
2. The worker has private R2 write access; API credentials remain read/presign
   only. Verify a bounded storage probe separately.
3. The live Highlight provider is explicitly enabled only in staging and the
   Actor publisher's customer-facing archival permission is recorded.
4. A server-owned job budget reserves no more than $0.65 total: up to $0.10
   for collection discovery and up to $0.55 for one collection enumeration.
   These are hard requested caps, not a prediction of final charges.

## One-Story procedure

1. Record the commercial-use approval, the public test-media authorization,
   and a one-time operator authorization reference. Explicitly approve a
   maximum aggregate charge of **$0.65**; this is a provider-request cap, not
   a guarantee of final billing.
2. Ensure only the staging worker receives the restricted Actor token, then
   set all four server-owned staging gates: media, live provider, canary, and
   operator canary. API and bot remain without the token. Do not place any
   value in browser configuration.
3. Use the normal owned Highlight metadata to select one pre-indexed **video**
   membership from the approved collection. The operator command accepts only
   owned internal target identifiers and a non-secret authorization reference:

   ```sh
   python -m ops.queue_highlight_story_canary \
     --user-id '<owned-user-uuid>' --telegram-chat-id '<owned-chat-id>' \
     --profile-id '<owned-profile-uuid>' --membership-id '<owned-video-membership-uuid>' \
     --authorization-reference '<approved-change-record>'
   ```

   It atomically creates one authorization, one normal media job, and one PGMQ
   message. A duplicate command returns the same pending job; it cannot create
   another authorization or queue message.
4. The worker reserves at most $0.10 for `collection_list` and $0.55 for
   `collection_items` before dispatch. It writes an opaque Actor run ID to the
   ledger immediately after each successful dispatch. A timeout before a
   receipt becomes indeterminate; a timeout after a receipt does not start a
   replacement run. Stop for operator reconciliation rather than retrying.
5. Download only the selected Story to worker temporary storage; validate
   bytes, size, type, and SHA-256 before private R2 persistence.
6. Verify exactly one ordered Highlight media-asset attachment and one shared
   stored object reference. Mint one owner-authorized short-lived playback
   capability and validate playback without recording the URL.
7. Stop. Disable the four gates. Do not archive another Story, collection,
   photo, or Telegram delivery.

## Evidence to retain

Retain sanitized job/invocation states, aggregate byte/attachment outcomes,
and the object-reference result. Never retain provider tokens, CDN descriptors,
signed URLs, storage keys, cookies, raw provider responses, or customer IDs in
the acceptance report.
