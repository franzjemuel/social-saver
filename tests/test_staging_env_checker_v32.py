import os, subprocess, sys
from pathlib import Path
SCRIPT = Path(__file__).parents[1] / "scripts" / "check-staging-env.py"

def run(role, values):
    env={"PATH":os.environ.get("PATH",""),"SOCIAL_SAVER_ROLE":role,**values}
    return subprocess.run([sys.executable,str(SCRIPT)],env=env,text=True,capture_output=True)

def test_bot_missing_keys_fail_without_values():
    r=run("bot",{})
    assert r.returncode == 1
    assert "DATABASE_URL" in r.stdout and "TELEGRAM_BOT_TOKEN" in r.stdout

def test_bot_minimum_passes():
    r=run("bot",{"DATABASE_URL":"secret-db","TELEGRAM_BOT_TOKEN":"secret-token"})
    assert r.returncode == 0
    assert "secret-db" not in r.stdout and "secret-token" not in r.stdout

def test_worker_requires_provider_and_storage_keys():
    r=run("worker",{"DATABASE_URL":"x","TELEGRAM_BOT_TOKEN":"x"})
    assert r.returncode == 1
    assert "SESSION_MASTER_KEY" in r.stdout and "R2_BUCKET" in r.stdout and "APIFY_API_TOKEN" in r.stdout

def test_bot_strict_rejects_apify_token():
    env={"PATH":os.environ.get("PATH", ""),"SOCIAL_SAVER_ROLE":"bot","DATABASE_URL":"x","TELEGRAM_BOT_TOKEN":"x","APIFY_API_TOKEN":"worker-only"}
    r=subprocess.run([sys.executable,str(SCRIPT),"--strict"],env=env,text=True,capture_output=True)
    assert r.returncode == 3
    assert "APIFY_API_TOKEN" in r.stdout and "worker-only" not in r.stdout

def test_bot_strict_rejects_worker_storage_secret():
    env={"PATH":os.environ.get("PATH",""),"SOCIAL_SAVER_ROLE":"bot","DATABASE_URL":"x","TELEGRAM_BOT_TOKEN":"x","R2_SECRET_ACCESS_KEY":"should-not-be-here"}
    r=subprocess.run([sys.executable,str(SCRIPT),"--strict"],env=env,text=True,capture_output=True)
    assert r.returncode == 3
    assert "R2_SECRET_ACCESS_KEY" in r.stdout
    assert "should-not-be-here" not in r.stdout

def test_api_strict_rejects_instagram_credentials():
    values={"DATABASE_URL":"x","TELEGRAM_BOT_TOKEN":"x","R2_ACCOUNT_ID":"x","R2_ACCESS_KEY_ID":"x","R2_SECRET_ACCESS_KEY":"x","R2_BUCKET":"x","INSTAGRAM_SESSION_PASSWORD":"nope"}
    env={"PATH":os.environ.get("PATH",""),"SOCIAL_SAVER_ROLE":"api",**values}
    r=subprocess.run([sys.executable,str(SCRIPT),"--strict"],env=env,text=True,capture_output=True)
    assert r.returncode == 3
    assert "INSTAGRAM_SESSION_PASSWORD" in r.stdout
    assert "nope" not in r.stdout
