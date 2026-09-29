# Telegram Stars subscription implementation

Digital services sold inside Telegram use XTR. Plus and Pro are recurring 30-day subscriptions.
The seeded 299/799 Star prices are placeholders until product pricing is finalized.

Security/integrity rules:
1. Invoice payload binds internal user UUID and plan.
2. Pre-checkout re-reads current price and verifies user, XTR currency, and amount.
3. `telegram_payment_charge_id` is unique, making successful-payment handling idempotent.
4. Entitlements activate only after `successful_payment`, never after invoice creation or pre-checkout.
5. Telegram can permit multiple concurrent subscriptions. Social Saver deliberately exposes one authoritative active plan per app user.
6. Keep charge IDs for refunds/support.
7. `/paysupport` is mandatory operational surface for digital-goods disputes.
8. Cancellation should call Telegram's subscription cancellation API using the saved Telegram charge ID, while keeping access until the current period ends. This is the next payment lifecycle increment.
