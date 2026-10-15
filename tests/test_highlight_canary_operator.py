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


def test_operator_command_does_not_require_worker_only_apify_token(monkeypatch):
    monkeypatch.setattr(command.settings, "app_env", "staging")
    monkeypatch.setattr(command.settings, "tiktok_highlights_media_enabled", True)
    monkeypatch.setattr(command.settings, "tiktok_highlights_live_provider_enabled", True)
    monkeypatch.setattr(command.settings, "tiktok_highlights_canary_enabled", True)
    monkeypatch.setattr(command.settings, "tiktok_highlights_canary_operator_enabled", True)
    monkeypatch.setattr(command.settings, "apify_tiktok_highlights_token", None)
    assert command._enabled() is True


@pytest.mark.asyncio
async def test_operator_command_rejects_invalid_identifiers_before_database_access(monkeypatch):
    monkeypatch.setattr(command, "_enabled", lambda: True)
    monkeypatch.setattr(command.settings, "app_env", "staging")
    args = SimpleNamespace(
        user_id="not-a-uuid", telegram_chat_id=1,
        profile_id="not-a-uuid", membership_id="not-a-uuid", authorization_reference="approved", dry_run=False,
    )
    assert await command.queue(args) is False


@pytest.mark.asyncio
async def test_operator_dry_run_reports_no_eligible_membership_without_provider_access(monkeypatch, capsys):
    class Database:
        def __init__(self, _url): self.pool = object()
        async def connect(self): pass
        async def close(self): pass

    class Repository:
        def __init__(self, _pool): pass
        async def has_owned_highlight_canary_candidate(self, *_args): return False

    monkeypatch.setattr(command, "_enabled", lambda: True)
    monkeypatch.setattr(command.settings, "app_env", "staging")
    monkeypatch.setattr(command, "Database", Database)
    monkeypatch.setattr(command, "Repository", Repository)
    args = SimpleNamespace(
        user_id="00000000-0000-0000-0000-000000000001", telegram_chat_id=None,
        profile_id="00000000-0000-0000-0000-000000000002", membership_id=None,
        authorization_reference=None, dry_run=True,
    )
    assert await command.queue(args) is False
    assert capsys.readouterr().out.strip() == "canary_preflight=no_eligible_membership"
