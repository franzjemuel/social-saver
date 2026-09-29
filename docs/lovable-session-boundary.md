# Lovable boundary for provider sessions

Lovable may show only sanitized provider health:
`healthy`, `cooldown`, `challenge`, `disabled`, last validated time, and a generic action-required message.

Lovable must never receive username/password, cookies, authorization headers, session IDs, encrypted_settings,
SESSION_MASTER_KEY, proxy credentials, or raw Instagram error payloads. Login/challenge handling stays in the worker/admin backend.
