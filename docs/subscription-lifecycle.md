# Subscription lifecycle

States:
active -> cancel_at_period_end=true -> expired
active -> renewed (period end advances)
active -> switched locally to another paid plan

Cancellation uses Telegram Bot API `editUserStarSubscription` and does not revoke already-paid access.
The initial Telegram charge ID is preserved separately from later renewal charge IDs.

Renewal handling is idempotent through unique `payment_events.telegram_payment_charge_id`.
A recurring payment updates the existing subscription period rather than creating a new logical subscription.

Run `python ops/expire_subscriptions.py` periodically in deployment so elapsed periods stop granting entitlements.
Production deployment should schedule this at least hourly.

Important remaining plan-switch rule: before exposing one-click Plus <-> Pro switching, cancel the old Telegram
subscription's auto-renewal first, otherwise Telegram can continue billing both subscriptions. v1.0 does not expose
a switch command for that reason.

Refunds remain an operator/support action. Telegram Bot API provides `refundStarPayment`; do not expose an automatic
self-service refund until a refund policy and archive/data consequences are defined.
