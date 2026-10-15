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

Migration 027 adds the final canary-only execution boundary. It creates one
operator-created, tenant-owned video authorization and extends the ledger with
a `dispatched` state plus a durable opaque Actor run ID. The worker reserves
at most 100,000 micro-USD for collection discovery and 550,000 micro-USD for
Story enumeration, so the aggregate requested cap cannot exceed 650,000
micro-USD ($0.65) for one authorization. It does not accept a budget, provider,
or target from a browser or queue payload.

The async Actor provider remains unregistered unless **all** staging-only
server gates are true: Highlight media, live provider, canary, and operator
canary. The token is worker-only. On an ambiguous dispatch without a receipt,
the reservation becomes `indeterminate`; after a receipt, ordinary redelivery
may only poll that same run and never start another paid run. This narrows the
duplicate-charge window but does not claim exactly-once external execution or
prove final provider billing. No gate is enabled by this code.
