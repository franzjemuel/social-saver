# Entitlements

Database values are authoritative. UI checks are cosmetic.

Seed defaults are placeholders:
Free: 20 public downloads/day, 0 archive, 0 watches, 0 Live.
Plus: 200/day, 10 GiB archive, 5 watches, 120 Live minutes/month.
Pro: 1000/day, 100 GiB archive, 25 watches, 1000 Live minutes/month.

Archive usage is logical customer-owned bytes. SHA256 deduplication reduces our R2 bill but does not reduce a customer's quota.
`quota_reservations` prevents concurrent jobs from overspending capacity. Fast anti-abuse rate limiting belongs in Redis later.
