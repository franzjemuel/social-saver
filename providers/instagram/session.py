import asyncio
from datetime import datetime, timezone

from instagrapi import Client
from instagrapi.exceptions import (
    BadCredentials, BadPassword, ChallengeRequired, FeedbackRequired,
    LoginRequired, PleaseWaitFewMinutes, TwoFactorRequired,
)

from core.config import settings
from core.provider_sessions import ProviderSessionStore


def _raise_provider_exception(client, exc):
    # Do not enter instagrapi's interactive challenge/password recovery.
    raise exc


def _require_manual_verification(*args, **kwargs):
    raise ChallengeRequired("Complete verification in the official Instagram app")


class InstagramSessionManager:
    LABEL = "beta-service"

    def __init__(self, pool):
        self.store = ProviderSessionStore(pool, settings.session_master_key)

    async def client(self, *, recover=False):
        if not settings.instagram_session_username:
            return None
        row = await self.store.get_or_create("instagram", self.LABEL)
        if row["status"] == "disabled":
            return None
        if row["cooldown_until"] and row["cooldown_until"] > datetime.now(timezone.utc):
            return None
        if not recover and (row["status"] == "challenge" or (
            row["status"] == "needs_login" and row["consecutive_failures"] > 0
        )):
            return None

        cl = Client()
        cl.handle_exception = _raise_provider_exception
        cl.challenge_code_handler = _require_manual_verification
        cl.change_password_handler = _require_manual_verification
        saved = self.store.settings_from_row(row)
        if saved:
            cl.set_settings(saved)
        if row["status"] == "healthy" and saved and cl.user_id:
            return cl
        if not settings.instagram_session_password:
            return None

        # Persist the device identity before any request, including interrupted
        # or failed logins. Only save_healthy may promote this to a usable session.
        await self.store.save_settings(row["id"], cl.get_settings())
        try:
            logged_in = await asyncio.to_thread(
                cl.login, settings.instagram_session_username, settings.instagram_session_password
            )
            if not logged_in or not cl.user_id:
                raise LoginRequired("Login did not establish an authenticated session")
        except Exception as exc:
            await self.store.save_settings(row["id"], cl.get_settings())
            await self.record_failure(exc, during_login=True)
            return None
        await self.store.save_healthy(row["id"], cl.get_settings())
        return cl

    async def record_failure(self, exc, *, during_login=False):
        row = await self.store.get_or_create("instagram", self.LABEL)
        if isinstance(exc, TwoFactorRequired):
            await self.store.mark(row["id"], "challenge", "TWO_FACTOR_REQUIRED")
        elif isinstance(exc, ChallengeRequired):
            await self.store.mark(row["id"], "challenge", "CHALLENGE_REQUIRED")
        elif isinstance(exc, (PleaseWaitFewMinutes, FeedbackRequired)):
            await self.store.mark(row["id"], "cooldown", "RATE_OR_FEEDBACK_BLOCK", 60)
        elif isinstance(exc, (BadPassword, BadCredentials)):
            await self.store.mark(row["id"], "needs_login", "CREDENTIALS_REJECTED")
        elif isinstance(exc, LoginRequired):
            await self.store.mark(row["id"], "needs_login", "LOGIN_REQUIRED")
        elif during_login:
            # Unknown login failures require operator recovery, never periodic
            # fresh-device login attempts or raw provider response logging.
            await self.store.mark(row["id"], "needs_login", "LOGIN_FAILED")
        else:
            await self.store.mark(row["id"], "cooldown", type(exc).__name__, 30)
