from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
API=(ROOT/'apps/api/main.py').read_text()
REPO=(ROOT/'core/repository.py').read_text()

def test_archive_detail_is_tenant_scoped():
    assert '@app.get("/v1/archive/{entry_id}")' in API
    assert 'get_owned_archive_entry(identity["app_user_id"], parsed)' in API
    assert 'ae.user_id=$2' in REPO

def test_download_authorizes_before_presigning():
    route=API.split('@app.post("/v1/archive/{entry_id}/assets/{asset_id}/download")',1)[1]
    assert 'get_owned_archive_object(identity["app_user_id"], entry, asset)' in route
    assert route.index('get_owned_archive_object') < route.index('presigned_get')

def test_archive_api_does_not_return_storage_key_in_detail():
    method=REPO.split('async def list_archive_assets_safe',1)[1].split('async def ',1)[0]
    assert 'storage_key' not in method

def test_save_can_request_archive_but_checks_entitlement():
    assert 'archive: bool = False' in API
    assert 'authorize_archive(uid)' in API
    assert '"archive": body.archive' in API
