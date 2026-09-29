from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_live_migration_has_job_id_and_reservation_ledger():
    sql=(ROOT/'supabase/migrations/018_live_leases.sql').read_text()
    assert 'live_sessions_job_id_uidx' in sql
    assert 'live_quota_reservations' in sql
    assert "status='reserved'" in sql

def test_live_processor_locks_before_reserving_and_reconciles():
    src=(ROOT/'apps/worker/processors/live.py').read_text()
    assert src.index('pg_try_advisory_lock') < src.index('quota.reserve')
    assert 'sum(duration_seconds)' in src
    assert 'reservation.reservation_id' in src

def test_queue_has_rolling_visibility_heartbeat():
    worker=(ROOT/'apps/worker/main.py').read_text()
    queue=(ROOT/'core/queue.py').read_text()
    cfg=(ROOT/'core/config.py').read_text()
    assert 'extend_visibility' in worker and 'pgmq.set_vt' in queue
    assert 'queue_heartbeat_seconds: int = 20' in cfg
    assert 'queue_long_job_visibility_seconds: int = 300' in cfg

def test_recovery_job_reconciles_persisted_segments():
    src=(ROOT/'ops/recover_live_leases.py').read_text()
    assert "r.expires_at < now()" in src
    assert 'sum(duration_seconds)' in src
    assert 'quota.settle' in src
