# TikTok Highlights live-provider activation gates

`providers/tiktok/apify_highlights_provider.py` is a mocked, unregistered
normalization adapter. It is not constructed by the API or worker and its
underlying POC refuses network calls unless its separate manual POC gate is
explicitly supplied. This branch therefore cannot execute a paid Actor call.

Before any future factory registers the adapter, review and implement a new
durable per-Actor-invocation ledger. It must record one invocation intent
before each collection-list or collection-story call, include a bounded
server-owned job budget and per-call cap, and mark ambiguous timeouts as
non-retriable until an operator resolves them. Synchronous dataset responses
do not establish final Apify charges; any run ID/receipt evidence must be
kept separately from estimated cost and never exposed to browser status APIs.

Required approval gates remain: provider commercial licensing and privacy/
retention review, explicit spending approval, timeout/recovery policy,
database migration review for the invocation ledger, and staged manual
activation. No token, signed descriptor, raw Actor response, media download,
R2 write, automatic polling, or customer-visible enablement belongs in this
adapter PR.
