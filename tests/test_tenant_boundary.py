from pathlib import Path
import inspect

from core.repository import Repository


def test_archive_repository_methods_are_actual_repository_methods():
    # Regression: v2.0 accidentally dedented these helpers to module scope,
    # which made /archive runtime calls fail with AttributeError.
    required = {
        "get_stored_object_by_sha",
        "create_stored_object",
        "get_or_create_archive_entry",
        "attach_archive_asset",
        "list_archive_entries",
        "list_archive_assets",
        "get_owned_archive_object",
        "soft_delete_archive_entry",
        "mark_stored_object_deleted",
    }
    assert required.issubset(set(dir(Repository)))


def test_owned_archive_lookup_binds_user_entry_and_asset():
    src = inspect.getsource(Repository.get_owned_archive_object).lower()
    assert "ae.user_id=$2" in src
    assert "ae.id=$1" in src
    assert "aea.media_asset_id=$3" in src
    assert "ae.deleted_at is null" in src
    assert "so.deleted_at is null" in src


def test_archive_list_and_delete_are_tenant_scoped():
    listing = inspect.getsource(Repository.list_archive_assets).lower()
    deletion = inspect.getsource(Repository.soft_delete_archive_entry).lower()
    assert "ae.user_id=$2" in listing
    assert "user_id=$2" in deletion


def test_supabase_data_api_is_closed_to_browser_roles():
    sql = Path("supabase/migrations/017_tenant_boundary.sql").read_text().lower()
    assert "revoke all privileges on all tables in schema public from anon, authenticated" in sql
    assert "revoke execute on all functions in schema public from anon, authenticated" in sql
    assert "alter default privileges" in sql
