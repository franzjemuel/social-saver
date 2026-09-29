import inspect
from core.repository import Repository


def test_cleanup_rechecks_live_references():
    src = inspect.getsource(Repository.list_unreferenced_objects_for_archive_entry).lower()
    assert "not exists" in src
    assert "deleted_at is null" in src

def test_delete_is_tenant_scoped():
    src = inspect.getsource(Repository.soft_delete_archive_entry).lower()
    assert "user_id=$2" in src

def test_api_queues_cleanup_not_r2_delete():
    from pathlib import Path
    src = Path("apps/api/main.py").read_text().lower()
    block = src[src.index('@app.delete("/v1/archive/{entry_id}"'):src.index('@app.post("/v1/archive/{entry_id}/assets')]
    assert "soft_delete_archive_entry" in block
    assert '"purge_archive"' in block
    assert ".storage.delete" not in block

def test_worker_owns_physical_delete():
    from pathlib import Path
    src = Path("apps/worker/processors/archive_delete.py").read_text().lower()
    assert "storage.delete" in src
    assert "list_unreferenced_objects_for_archive_entry" in src


def test_bot_queues_cleanup_not_r2_delete():
    from pathlib import Path
    src = Path("apps/bot/main.py").read_text().lower()
    block = src[src.index('@dp.callback_query(f.data.startswith("ar:yes:"))'):src.index('@dp.message(command("lives"))')]
    assert "soft_delete_archive_entry" in block
    assert '"purge_archive"' in block
    assert "queue.send" in block
    assert ".delete(" not in block
    assert "mark_stored_object_deleted" not in block
