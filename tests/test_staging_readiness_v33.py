import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("staging_readiness", ROOT/"scripts/staging-readiness.py")
MOD=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)

def test_readiness_report_tracks_current_migration_floor():
    report=MOD.build_report()
    assert report["latest_migration"] == 22
    assert report["ok"] is True

def test_worker_has_provider_and_write_storage_secrets():
    worker=set(MOD.SERVICES["worker"]["required"])
    assert {"SESSION_MASTER_KEY","INSTAGRAM_SESSION_USERNAME","INSTAGRAM_SESSION_PASSWORD"} <= worker
    assert {"R2_ACCESS_KEY_ID","R2_SECRET_ACCESS_KEY"} <= worker

def test_bot_does_not_receive_provider_or_storage_secrets():
    bot=set(MOD.SERVICES["bot"]["required"])
    assert bot == {"DATABASE_URL","TELEGRAM_BOT_TOKEN"}
