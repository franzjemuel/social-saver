# R2 lifecycle policy

Keep `archive/` durable. Expire only temporary `overflow/` objects.

Recommended launch rule:

```bash
npx wrangler r2 bucket lifecycle add "$R2_BUCKET" \
  --id delete-overflow-after-2-days \
  --prefix overflow/ \
  --expire-days 2
```

Verify in the Cloudflare dashboard before production. Never apply an unfiltered
expiration rule to this bucket because `archive/` objects are customer-owned
persistent data.

Do not configure a bucket lock on `archive/` at launch. Users need to be able
to delete their own archived objects, and bucket locks take precedence over
lifecycle deletion.
