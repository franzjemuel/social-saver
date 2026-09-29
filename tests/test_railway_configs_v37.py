from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_all_railway_service_configs_exist_and_pin_dockerfiles():
    expected = {"railway.bot.toml": "Dockerfile", "railway.api.toml": "Dockerfile.api", "railway.worker.toml": "Dockerfile.worker"}
    for filename, dockerfile in expected.items():
        text = (ROOT / filename).read_text()
        assert f'dockerfilePath = "{dockerfile}"' in text
        assert "watchPatterns" in text

def test_api_has_deploy_healthcheck_only():
    api = (ROOT / "railway.api.toml").read_text()
    bot = (ROOT / "railway.bot.toml").read_text()
    worker = (ROOT / "railway.worker.toml").read_text()
    assert 'healthcheckPath = "/health"' in api
    assert "healthcheckPath" not in bot
    assert "healthcheckPath" not in worker

def test_ci_builds_all_three_images():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for dockerfile in ("Dockerfile", "Dockerfile.api", "Dockerfile.worker"):
        assert f"-f {dockerfile} ." in ci
