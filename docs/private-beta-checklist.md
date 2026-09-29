# Private beta checklist

Ship first: public Instagram link saving, Telegram delivery, optional R2 archive, post/Story watches, plan/usage
display, Stars test flow, sanitized status, and operator session health.

Keep disabled: customer-facing `/watchlive`, Facebook Stories, customer Instagram password collection, arbitrary
browser automation, unsafe plan switching, and a public Lovable dashboard.

Exit criterion: 20 invited testers and 7 consecutive days without cross-user leakage, payment duplication, queue
loss, or unrecoverable archive corruption. Provider failures may occur but must degrade to explicit errors/retries.

## v2.3 Live source security gate
- [ ] `LIVE_ALLOW_MANUAL_SOURCE=false` in any customer-facing environment.
- [ ] Customer payload cannot select `manifest_url`, headers, cookies, or FFmpeg options.
- [ ] Recorder service has no public ingress.
- [ ] Recorder egress cannot reach loopback, RFC1918/private networks, link-local or cloud metadata endpoints.
- [ ] Instagram provider-generated headers work against a controlled real Live test.
- [ ] Customer Live remains disabled until the network egress test above passes.
