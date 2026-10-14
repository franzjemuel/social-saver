# TikTok Highlight one-Story staging acceptance

This runbook is intentionally inactive until provider licensing, migration 025,
durable per-invocation budget accounting, and an explicit staging authorization
have all been approved. It never authorizes production use.

## Preconditions

1. Staging API and worker run the reviewed feature commit; migration 025 is
   applied through the normal controlled release procedure.
2. The worker has private R2 write access; API credentials remain read/presign
   only. Verify a bounded storage probe separately.
3. The live Highlight provider is explicitly enabled only in staging and the
   Actor publisher's customer-facing archival permission is recorded.
4. A server-owned job budget reserves no more than $0.65 total: up to $0.10
   for collection discovery and up to $0.55 for one collection enumeration.
   These are hard requested caps, not a prediction of final charges.

## One-Story procedure

1. Submit the approved test profile, `theyuh_lvsh`, to the manually authorized
   discovery flow. Do not schedule a rescan.
2. From the discovered `ako` collection, select exactly one provider-returned
   video Story in worker memory. Do not put its descriptor in a queue payload,
   database row, log, browser response, or ticket.
3. Claim the durable provider invocation before each Actor run. If a provider
   timeout is ambiguous, mark it indeterminate and stop; do not retry it.
4. Download only the selected Story to worker temporary storage; validate
   bytes, size, type, and SHA-256 before private R2 persistence.
5. Verify exactly one ordered Highlight media-asset attachment and one shared
   stored object reference. Mint one owner-authorized short-lived playback
   capability and validate playback without recording the URL.
6. Stop. Do not archive another Story, collection, photo, or Telegram delivery.

## Evidence to retain

Retain sanitized job/invocation states, aggregate byte/attachment outcomes,
and the object-reference result. Never retain provider tokens, CDN descriptors,
signed URLs, storage keys, cookies, raw provider responses, or customer IDs in
the acceptance report.
