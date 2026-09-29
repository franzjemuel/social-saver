# Live source security boundary — v2.3

## Decision

A customer-facing job may identify a provider and target, but it may not supply the URL that FFmpeg opens. The worker resolves the stream through the provider adapter. Manual manifests are disabled by default (`LIVE_ALLOW_MANUAL_SOURCE=false`) and exist only for controlled local/staging tests.

This closes the highest-risk application-level SSRF path: untrusted URL -> privileged FFmpeg process.

## Defense layers

1. Provider-side resolution: `record_live` resolves Instagram inside the worker. Future Facebook support implements the same `LiveSourceResolver` boundary.
2. URL validation: HTTPS only, default port only, no URL credentials, and preflight rejection of private, loopback, link-local, multicast, reserved and metadata-address space.
3. FFmpeg protocol restriction: input protocol whitelist is `https,http,tcp,tls,crypto`; `file`, `data`, `concat`, `ftp`, and other unrelated protocols are not enabled.
4. Header boundary: only User-Agent, Referer, Cookie, Authorization and Origin can reach FFmpeg. CR/LF injection is rejected. Headers are never logged by Social Saver.
5. Production isolation: treat FFmpeg as a network-capable untrusted parser. The recorder should run with no cloud control-plane credentials and no public ingress. Network-level egress blocking for RFC1918, loopback, link-local and cloud metadata is still required because DNS can change after application validation and manifests can reference child resources.

## Credential handling

FFmpeg supports HTTP `user_agent`, `referer`, custom `headers`, and cookies. v2.3 passes only sanitized provider-generated values. Because command-line options can be visible to other processes in the same OS namespace, do not colocate untrusted tenant processes in the recorder container. A future stronger design is a local fetch proxy that injects credentials without placing them in FFmpeg argv.

## Remaining release block

Application validation cannot guarantee that every HLS/DASH child URL remains public after DNS rebinding or redirects. Before customer Live is enabled, enforce private-network and metadata egress denial at the recorder service/network layer, or route media fetches through a hardened outbound proxy. Customer Live remains disabled until this is verified in staging.

## Lovable boundary

Lovable may show recording controls/status. It must never accept or forward a manifest URL, cookies, provider headers, session secrets, or FFmpeg options. The UI submits only a provider target and requested duration to the backend.
