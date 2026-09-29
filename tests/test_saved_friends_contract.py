from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_saved_friend_migration_has_one_minute_floor():
    s=(ROOT/'supabase/migrations/015_saved_friends.sql').read_text()
    assert 'poll_interval_seconds >= 60' in s
    assert 'saved_friend boolean' in s
def test_saved_friend_jobs_request_archive():
    s=(ROOT/'core/watches.py').read_text()
    assert '"archive":bool(watch["auto_archive"])' in s
def test_bot_exposes_saved_friend_commands():
    s=(ROOT/'apps/bot/main.py').read_text()
    for cmd in ('savefriend','friends','removefriend'):
        assert f'Command("{cmd}")' in s
