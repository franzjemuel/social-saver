#!/usr/bin/env python3
"""Offline staging readiness report.

Validates repository/deployment contracts without contacting providers or printing
secrets. This is safe to run in CI and before the destructive-safe online canary.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
SERVICES = {
    "bot": {"dockerfile": "Dockerfile", "required": {"DATABASE_URL", "TELEGRAM_BOT_TOKEN"}},
    "api": {"dockerfile": "Dockerfile.api", "required": {"DATABASE_URL", "TELEGRAM_BOT_TOKEN", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"}},
    "worker": {"dockerfile": "Dockerfile.worker", "required": {"DATABASE_URL", "TELEGRAM_BOT_TOKEN", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "SESSION_MASTER_KEY", "INSTAGRAM_SESSION_USERNAME", "INSTAGRAM_SESSION_PASSWORD"}},
}
OPTIONAL = {"UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "SENTRY_DSN"}


def migration_versions() -> list[int]:
    versions=[]
    for path in MIGRATIONS.glob("*.sql"):
        m=re.match(r"(\d+)_", path.name)
        if m: versions.append(int(m.group(1)))
    return sorted(versions)


def check(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def build_report() -> dict:
    checks=[]
    versions=migration_versions()
    expected=list(range(1, max(versions, default=0)+1))
    checks.append(check("migration_chain", bool(versions) and versions == expected, f"001..{versions[-1]:03d}" if versions else "none"))
    for service, spec in SERVICES.items():
        checks.append(check(f"{service}_dockerfile", (ROOT/spec["dockerfile"]).is_file(), spec["dockerfile"]))
    railway_configs = {"bot": "railway.bot.toml", "api": "railway.api.toml", "worker": "railway.worker.toml"}
    for service, config in railway_configs.items():
        path = ROOT / config
        text = path.read_text() if path.is_file() else ""
        dockerfile = SERVICES[service]["dockerfile"]
        checks.append(check(f"{service}_railway_config", path.is_file() and f'dockerfilePath = "{dockerfile}"' in text, config))
    checks.append(check("online_canary", (ROOT/"ops/staging_canary.py").is_file(), "ops/staging_canary.py"))
    checks.append(check("ci_gate", (ROOT/".github/workflows/ci.yml").is_file(), ".github/workflows/ci.yml"))
    return {
        "version": "3.8",
        "ok": all(c["ok"] for c in checks),
        "latest_migration": versions[-1] if versions else None,
        "checks": checks,
        "service_secret_names": {k: sorted(v["required"]) for k,v in SERVICES.items()},
        "optional_secret_names": sorted(OPTIONAL),
        "next_online_gate": "./scripts/staging-canary.sh",
    }


def main() -> None:
    report=build_report()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        for item in report["checks"]:
            print(("PASS" if item["ok"] else "FAIL"), item["check"], "-", item["detail"])
        print(f"LATEST MIGRATION {report['latest_migration']:03d}" if report["latest_migration"] else "LATEST MIGRATION none")
        print("OFFLINE CONTRACTS PASS; HOSTED SERVICES NOT VERIFIED" if report["ok"] else "NOT READY")
    raise SystemExit(0 if report["ok"] else 1)

if __name__ == "__main__":
    main()
