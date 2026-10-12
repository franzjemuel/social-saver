# TikTok Highlights Apify discovery POC

This is an isolated, manually invoked evaluation of the community-maintained
Apify Actor resource `25j9k3sawJYKrKL2X` (published as
`igview-owner/tiktok-highlight-scraper`). It is not part of the API,
worker, profile importer, database schema, or automatic rescan pipeline.

## Published Actor contract evaluated

The Actor documents two input modes:

- `highlight_list` with one `uniqueIds` username returns collection records
  containing `highlight_id`, `title`, optional `cover`, and `post_count`.
- `highlight_posts` with one Actor-returned `highlightIds` value and
  `postsPerPage` from 1 through 30 returns video or photo records. The Actor
  says it paginates internally; its flattened dataset does not expose cursors
  or page boundaries. This POC therefore reports only that returned count was
  greater than the requested page size.

Collection and item IDs are **provider-returned identifiers**. They are not
claimed to be TikTok-native or durable until repeated live runs establish that.

## Confirmed authorized live POC results

One authorized, metadata-only experiment completed successfully for
`theyuh_lvsh`. Collection discovery returned two named collections: `💗`
(reported 44 Stories) and `ako` (reported 25 Stories). The provider-returned
collection identifiers were retained only for this POC assessment.

One authorized enumeration of the 25-item collection returned exactly 25
unique Story records, with normalized positions 0 through 24 and no duplicate
provider item identifiers. Each observed record was a video and included a
media descriptor, cover descriptor, creation timestamp, and duration. The
returned order appeared descending by creation date. The requested batch size
was 10 while the flattened response contained 25 records; this confirms the
provider returned more than the requested batch, but not its internal cursor
mechanics or TikTok-native ordering.

No raw provider response, token, signed media URL, or media byte was stored by
the POC.

## Manual command

Dry runs validate the exact Actor payload without reading a token or using the
network:

```sh
python -m providers.tiktok.apify_highlights \
  --mode highlight_list --username theyuh_lvsh --max-charge-usd 0.10
```

A paid metadata-only call requires all of the following:

1. operator approval for that exact run;
2. `APIFY_TIKTOK_HIGHLIGHTS_TOKEN` in the process environment;
3. `SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE=1` in the process environment; and
4. `--live` plus an explicit max charge no greater than USD 1.00.

The selected documented synchronous endpoint accepts `maxTotalChargeUsd` and
`timeout` as **query parameters**, not Actor input fields. The adapter sends
both, caps the response at 100 dataset items, and allows a run timeout from 1
through 300 seconds. Apify documents the charge parameter as a platform budget
cap; its outcome can still be a failed run or a partial dataset depending on
the Actor's pricing and behavior. This POC maps an HTTP charge-limit response
to a sanitized error and can reject a result whose unique item count differs
from an explicit expected count. It cannot independently attest an Actor's
final charge from the synchronous dataset response.

For the single authorized collection-discovery experiment, after an operator
has supplied the token only in their local process environment:

```sh
SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE=1 \
python -m providers.tiktok.apify_highlights \
  --mode highlight_list --username theyuh_lvsh \
  --max-charge-usd 0.10 --live
```

Do not run that command until an operator expressly approves the paid call.

If collection discovery succeeds and the returned collection ID is confirmed,
the separately bounded enumeration experiment is:

```sh
SOCIAL_SAVER_HIGHLIGHTS_POC_LIVE=1 \
python -m providers.tiktok.apify_highlights \
  --mode highlight_posts --collection-id 929610542340 \
  --posts-per-page 10 --expected-items 25 \
  --max-charge-usd 0.60 --live
```

The command prints only counts, identifiers, metadata presence, timestamps,
and media kinds. It does not print provider media URLs, covers, token values,
or raw responses. It never downloads media.

## Known limitations and production decision gates

- The Actor is community-maintained. Its license, privacy/retention terms,
  source acquisition method, reliability, and change-notice process require
  vendor review before any product dependency.
- The published contract claims internal cursor pagination, but does not return
  cursor/page provenance in its dataset output.
- A successful synchronous response has no final run receipt or charge total.
  Apify documents the budget cap and the POC sends it on every run, but one
  deliberate over-limit experiment has not been authorized.
- Photo ordering and upstream TikTok ordering are not proven by this POC.
- CDN descriptors are retained only in memory for a future worker evaluation;
  no descriptor is persisted or exposed by this command.
- The observed experimental runs cost roughly USD 0.044 for collection
  discovery and USD 0.504 for a 25-item enumeration. Treat these as samples,
  not pricing guarantees.

If vendor review and repeatability testing pass, a separate design PR should
add a worker-owned discovery adapter, dedicated Highlight collection/member
schema, tenant-scoped jobs, and explicit media-acquisition policy. It should
reuse existing R2 persistence only after ordering and identifier semantics are
validated.
