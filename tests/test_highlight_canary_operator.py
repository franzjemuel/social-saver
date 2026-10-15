from types import SimpleNamespace

import pytest

import ops.queue_highlight_story_canary as command


def test_operator_command_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(command.settings, "app_env", "staging")
    monkeypatch.setattr(command.settings, "tiktok_highlights_media_enabled", False)
    monkeypatch.setattr(command.settings, "tiktok_highlights_live_provider_enabled", False)
    monkeypatch.setattr(command.settings, "tiktok_highlights_canary_enabled", False)
    monkeypatch.setattr(command.settings, "tiktok_highlights_canary_operator_enabled", False)
    monkeypatch.setattr(command.settings, "apify_tiktok_highlights_token", None)
    assert command._enabled() is False


@pytest.mark.asyncio
async def test_operator_command_rejects_invalid_identifiers_before_database_access(monkeypatch):
    monkeypatch.setattr(command, "_enabled", lambda: True)
    args = SimpleNamespace(
        user_id="not-a-uuid", telegram_chat_id=1,
        profile_id="not-a-uuid", membership_id="not-a-uuid", authorization_reference="approved",
    )
    assert await command.queue(args) is False
