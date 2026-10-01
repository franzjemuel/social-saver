import pytest

from core.admin import parse_admin_telegram_ids


def test_admin_telegram_ids_parse_and_deduplicate():
    assert parse_admin_telegram_ids("123, 456,123") == frozenset({123,456})
    assert parse_admin_telegram_ids("") == frozenset()


@pytest.mark.parametrize("value",["abc","123,-2","0"])
def test_admin_telegram_ids_reject_invalid_values(value):
    with pytest.raises(ValueError,match="positive numeric IDs"):
        parse_admin_telegram_ids(value)


def test_bot_admins_bypass_watch_limits_and_can_read_id():
    source=open("apps/bot/main.py").read()
    assert 'Command("id")' in source
    assert source.count('not is_admin(message) and await watches.count_active') == 2
    assert 'Watch slots: Unlimited' in source
    assert 'Downloads: Unlimited' in source
    assert 'Live recording: Unlimited' in source


def test_unlimited_override_applies_across_services():
    migration=open("supabase/migrations/021_unlimited_access_overrides.sql").read()
    entitlements=open("core/entitlements.py").read()
    live_quota=open("core/live_quota.py").read()
    api=open("apps/api/main.py").read()
    assert "unlimited_access_overrides" in migration
    assert "unlimited_access_overrides" in entitlements
    assert "unlimited_access_overrides" in live_quota
    assert "if not unlimited" in api
