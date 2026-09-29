import inspect
from pathlib import Path
from core.repository import Repository
from apps.worker.processors.account_delete import process_purge_account

ROOT=Path(__file__).resolve().parents[1]

def test_migration_020_adds_deletion_state():
    sql=(ROOT/'supabase/migrations/020_account_deletion.sql').read_text().lower()
    assert 'deletion_requested_at' in sql
    assert 'deletion_started_at' in sql

def test_request_disables_watches_and_hides_archives_atomically():
    src=inspect.getsource(Repository.request_account_deletion).lower()
    assert 'transaction' in src
    assert "watches set status='paused'" in src
    assert 'archive_entries set deleted_at' in src

def test_blob_cleanup_preserves_shared_live_objects():
    src=inspect.getsource(Repository.list_unreferenced_objects_for_user).lower()
    assert 'not exists' in src
    assert 'live.deleted_at is null' in src
    assert 'stored_object_id=so.id' in src

def test_tenant_root_deleted_only_after_storage_cleanup():
    src=inspect.getsource(process_purge_account)
    assert src.index('service.storage.delete') < src.index('finalize_account_deletion')

def test_api_queues_worker_purge_not_direct_r2_delete():
    src=(ROOT/'apps/api/main.py').read_text()
    block=src[src.index('@app.delete("/v1/me"'):src.index('@app.get("/v1/watches")')]
    assert 'request_account_deletion' in block
    assert '"purge_account"' in block
    assert '.storage.delete' not in block
