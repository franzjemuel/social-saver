from pathlib import Path

ROOT = Path(__file__).parents[1]
API = (ROOT / "apps/api/main.py").read_text()
DOCKER = (ROOT / "Dockerfile.api").read_text()


def test_api_never_accepts_browser_user_id_parameter():
    assert "Depends(current_user_id)" in API
    assert "verify_telegram_init_data" in API
    assert "app_user_id: " not in API
    assert "app_user_id=" not in API


def test_initial_customer_endpoints_exist():
    for route in ("/health", "/v1/me", "/v1/dashboard", "/v1/archive", "/v1/watches"):
        assert route in API


def test_archive_and_watch_limits_are_bounded():
    assert "le=100" in API


def test_api_has_independent_container():
    assert "uvicorn apps.api.main:app" in DOCKER
