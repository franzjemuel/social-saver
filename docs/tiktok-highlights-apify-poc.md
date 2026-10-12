# TikTok Highlights Apify discovery POC

This is an isolated, manually invoked evaluation of the community-maintained
Apify Actor `igview-owner/tiktok-highlight-scraper`. It is not part of the API,
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

For example, after an authorized operator sets those environment variables:

```sh
python -m providers.tiktok.apify_highlights \
  --mode highlight_posts --collection-id example-collection \
  --posts-per-page 10 --max-charge-usd 0.60 --live
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
