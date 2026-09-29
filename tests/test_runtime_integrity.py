from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_worker_dispatches_all_long_running_job_types():
    source=(ROOT/"apps/worker/main.py").read_text()
    for job_type in ("resolve_media","poll_watch","record_live","finalize_live"):
        assert f'job["job_type"] == "{job_type}"' in source

def test_compose_uses_ffmpeg_worker_image():
    compose=(ROOT/"docker-compose.yml").read_text()
    assert "dockerfile: Dockerfile.worker" in compose

def test_health_table_has_migration():
    migrations="\n".join(p.read_text() for p in (ROOT/"supabase/migrations").glob("*.sql"))
    assert "create table if not exists worker_heartbeats" in migrations
