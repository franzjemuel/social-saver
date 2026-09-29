# v1.6 runtime integrity + Live discovery lab

A review of the generated v1.5 artifact found a serious integration defect: several Telegram decorators had escaped `main()` and referenced `dp` at module import time. Compile-only validation did not catch it. v1.6 rewrites the bot registration surface so handlers are created only after Bot, Dispatcher, database, queue, entitlements, payments, watches, and rate limiting exist. Previously intended `/subscription`, `/cancelplan`, `/watch`, `/watchstories`, `/watchboth`, `/watches`, `/unwatch`, and `/lives` commands are now actually registered.

Live source research remains intentionally conservative. Current public GitHub evidence still shows Instagram private responses with broadcast IDs and DASH playback fields, but does not establish an exhaustive stable endpoint for arbitrary watched accounts. `InstagramLiveProbe` tests authenticated `discover/top_live/` only as an experimental positive-signal strategy. A miss is `unknown`, not `offline`.

The next validation gate is an import/startup smoke test with test configuration plus a sanitized fixture captured from a legitimate public Live visible to the service account. Do not expose `/watchlive` until detection is reproducible before, during, and after a broadcast.
