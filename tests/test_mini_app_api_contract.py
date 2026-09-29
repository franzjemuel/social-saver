from pathlib import Path
from fastapi.routing import APIRoute
from apps.api.main import app, current_identity

ROOT = Path(__file__).parents[1]
API = (ROOT / "apps/api/main.py").read_text()
DOCKER = (ROOT / "Dockerfile.api").read_text()


def test_api_never_accepts_browser_user_id_parameter():
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith('/v1/')]
    assert routes
    for route in routes:
        assert current_identity in [d.call for d in route.dependant.dependencies], route.path
        supplied = route.dependant.query_params + route.dependant.path_params + route.dependant.body_params
        assert not {p.name for p in supplied} & {'app_user_id', 'user_id', 'telegram_user_id'}
    schema = app.openapi()
    for model in schema['components']['schemas'].values():
        assert not set(model.get('properties', {})) & {'app_user_id', 'user_id', 'telegram_user_id'}


def test_initial_customer_endpoints_exist():
    for route in ("/health", "/v1/me", "/v1/dashboard", "/v1/archive", "/v1/watches"):
        assert route in API


def test_archive_and_watch_limits_are_bounded():
    assert "le=100" in API


def test_api_has_independent_container():
    assert "uvicorn apps.api.main:app" in DOCKER
