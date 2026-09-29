# v3.1 Mini App archive detail and secure download

## Goal
Make the existing archive usable from the Telegram Mini App without exposing R2 object keys or credentials.

## API

`GET /v1/archive/{entry_id}` returns owned metadata plus customer-safe asset metadata. Foreign and missing IDs both return `404 archive_not_found`.

`POST /v1/archive/{entry_id}/assets/{asset_id}/download` performs tenant authorization first, then returns a short-lived R2 GET presigned URL. The URL is a bearer capability and must not be persisted by the frontend.

`POST /v1/save` now accepts `{"url":"...","archive":true}`. Archive requests are checked against the user's plan before queueing. The worker remains the only component that downloads Instagram media and writes archive bytes.

## Lovable implementation
Lovable can build an Archive detail page with media metadata and a Download button. On click, call the download endpoint and immediately navigate to the returned URL. Do not save the URL to localStorage, analytics, logs, or the database. If it expires, request a new one.

## Backend boundary
Lovable: archive list/detail UI, download click, save-with-archive toggle.

API: Telegram authentication, tenant checks, entitlement check, short-lived signing.

Worker: Instagram extraction, media download, archive upload/deduplication.

R2: private object storage. Keep the bucket private. Give the API a separate R2 API token scoped to Object Read only. Give the worker a separate write-capable token.

## R2 browser configuration
A browser following a presigned GET to the R2 S3 endpoint can download directly. If the frontend needs to fetch/read the response in JavaScript, configure R2 bucket CORS for the exact production Mini App origin and GET/HEAD only. Direct navigation/download does not require giving Lovable R2 credentials.

## Beta acceptance tests
1. User A can open A's archive detail.
2. User B receives the same 404 for A's entry as for a nonexistent entry.
3. User A can mint a URL for A's asset and download it.
4. User B cannot mint a URL using A's entry and asset IDs.
5. Detail/list JSON never contains `storage_key`.
6. Expired signed URLs fail and the UI requests a fresh URL.
7. Free/non-archive plan cannot submit `archive=true`.
8. Archive-capable plan can submit it and the worker creates exactly one archive entry.

## Production note
Use a short signing TTL. Current default is 900 seconds. For public beta, consider 300 seconds unless large downloads make that inconvenient.
