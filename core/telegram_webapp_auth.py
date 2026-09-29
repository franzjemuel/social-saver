"""Telegram Mini App initData verification.

Never trust initDataUnsafe from the browser. The raw initData string must be
verified server-side with the bot token before it can identify an app user.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class TelegramWebAppAuthError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramWebAppIdentity:
    telegram_user_id: int
    username: str | None
    first_name: str | None
    auth_date: int
    query_id: str | None


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 300,
    now: int | None = None,
) -> TelegramWebAppIdentity:
    """Verify Telegram Mini App initData using Telegram's HMAC scheme.

    Raises TelegramWebAppAuthError for malformed, forged, or stale payloads.
    A short max age limits replay of copied initData bearer material.
    """
    if not init_data or not bot_token:
        raise TelegramWebAppAuthError("missing init data or bot token")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise TelegramWebAppAuthError("missing hash")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramWebAppAuthError("invalid signature")

    try:
        auth_date = int(pairs["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramWebAppAuthError("invalid auth_date") from exc

    current = int(time.time()) if now is None else int(now)
    if auth_date > current + 30 or current - auth_date > max_age_seconds:
        raise TelegramWebAppAuthError("stale init data")

    try:
        user = json.loads(pairs["user"])
        telegram_user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TelegramWebAppAuthError("invalid user") from exc

    return TelegramWebAppIdentity(
        telegram_user_id=telegram_user_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        auth_date=auth_date,
        query_id=pairs.get("query_id"),
    )
