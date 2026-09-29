from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_adaptive_polling_schema():
    s=(ROOT/'supabase/migrations/016_adaptive_watch_polling.sql').read_text()
    for token in ('polling_state','idle_poll_count','last_new_item_at'):
        assert token in s

def test_watch_service_has_fast_warm_idle_cadence():
    s=(ROOT/'core/watches.py').read_text()
    assert "then 60" in s
    assert "then 180" in s
    assert "else 900" in s
    assert "new_items=0" in s

def test_worker_reports_new_items_to_scheduler():
    s=(ROOT/'apps/worker/processors/watch.py').read_text()
    assert 'new_items=queued' in s
