from pathlib import Path
ROOT = Path(__file__).parents[1]
API=(ROOT/'apps/api/main.py').read_text()
REPO=(ROOT/'core/repository.py').read_text()
CONFIG=(ROOT/'core/config.py').read_text()

def test_owned_job_status_is_tenant_scoped():
    assert 'where id=$1 and user_id=$2' in REPO
    assert 'get_owned_job_status' in REPO

def test_job_status_endpoint_exists_and_hides_foreign_jobs():
    assert '/v1/jobs/{job_id}' in API
    assert 'job_not_found' in API
    assert 'get_owned_job_status(identity["app_user_id"]' in API

def test_customer_projection_excludes_sensitive_job_fields():
    projection=REPO.split('async def get_owned_job_status',1)[1].split('async def start_job',1)[0]
    for forbidden in ('telegram_chat_id','input,','result,','error_message'):
        assert forbidden not in projection

def test_cors_is_explicit_and_configurable():
    assert 'api_cors_origins' in CONFIG
    assert 'CORSMiddleware' in API
    assert 'allow_origins=_cors_origins' in API
    assert 'allow_origins=["*"]' not in API
