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
