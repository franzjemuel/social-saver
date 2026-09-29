# Instagram authenticated session boundary

v1.2 adds one backend-only beta service session. It is not a customer credential collection flow.

The worker loads a stable instagrapi settings object from Postgres, encrypted with SESSION_MASTER_KEY.
Username/password exist only in the deployment secret store and are used only when no reusable healthy session exists.
Saved settings preserve device identifiers and authorization state. Challenges and rate/feedback blocks move the
session into `challenge` or `cooldown` instead of retrying fresh logins.

Operational rules:
* Never expose Instagram credentials, cookies, session IDs, or decrypted settings to Telegram, Lovable, logs, queue payloads, or browser code.
* Never ask customers to send Instagram passwords through Telegram.
* Use the official Instagram app for manual challenge recovery.
* Reuse stable session/device/network identity. Do not rotate identities to evade platform controls.
* Public extraction remains first choice for ordinary public posts. Authenticated access is a fallback and enables Story discovery.
* This is an unofficial integration and can break when Instagram changes private/public endpoints. Do not promise guaranteed capture.
* Only retrieve content the authenticated account is permitted to view. Do not attempt to bypass privacy controls.

Story support in this increment is discovery infrastructure only. `InstagramStoryDiscovery` returns canonical Story URLs
and provider-neutral IDs. The next increment should add a Story resolver that converts Story objects into the existing
ResolvedMedia contract and routes them through Telegram/R2.
