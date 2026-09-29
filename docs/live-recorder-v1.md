# Bounded Live recorder prototype

This increment deliberately solves the expensive recording/storage side before pretending Instagram Live source
discovery is stable.

Implemented:
* provider-neutral `LiveSource` contract
* monthly Live-minute reservation and settlement
* one globally active recorder using a PostgreSQL advisory lock
* hard per-recording duration cap
* FFmpeg stream-copy segmentation into 60-second MPEG-TS chunks
* upload of completed chunks to R2 while recording continues
* heartbeat, status, byte count, recorded seconds, and segment ledger
* worker Dockerfile that installs FFmpeg
* manual HLS test harness

Why segments instead of one giant MP4:
a worker crash loses at most the in-progress segment, not an entire multi-hour recording. Segments can be uploaded
continuously, keeping ephemeral disk requirements bounded. A later finalize job can concatenate/remux segments into
a user-friendly MP4 without transcoding when codecs permit.

R2 is a good fit: current multipart uploads support objects up to roughly 5 TiB, but this prototype avoids needing
one giant multipart object by writing immutable segment objects. R2 Standard is currently $0.015/GB-month and has
no Internet egress charge.

FFmpeg uses the segment muxer with stream copy. Segment boundaries follow source keyframes, so 60 seconds is a target,
not an exact guarantee.

NOT IMPLEMENTED YET:
Instagram viewer-side Live discovery/manifest resolution. Current instagrapi releases have livestream helpers for
creating an account's own broadcast, but that is different from reliably obtaining another account's playback
manifest. Do not ship a fake or brittle method. The `LiveSourceResolver` boundary exists so Instagram, Facebook,
or a hosted provider can be plugged in later.

Legal/platform boundary:
record only streams the service account is legitimately permitted to view. Do not bypass private-account controls,
access gates, DRM, or platform challenges. Treat unofficial Instagram access as best-effort.
