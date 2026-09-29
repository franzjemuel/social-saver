#!/usr/bin/env python3
"""Validate per-service staging secrets without printing secret values.

Use --strict in hosted staging/prod to fail when a service has high-risk secrets it
should not possess. This turns least privilege from documentation into a gate.
"""
import argparse, os, sys

SHARED={"DATABASE_URL","TELEGRAM_BOT_TOKEN"}
STORAGE={"R2_ACCOUNT_ID","R2_ACCESS_KEY_ID","R2_SECRET_ACCESS_KEY","R2_BUCKET"}
PROVIDER={"SESSION_MASTER_KEY","INSTAGRAM_SESSION_USERNAME","INSTAGRAM_SESSION_PASSWORD"}
OPTIONAL={"UPSTASH_REDIS_REST_URL","UPSTASH_REDIS_REST_TOKEN","SENTRY_DSN"}
REQUIRED={
    "bot": SHARED,
    "api": SHARED | STORAGE,
    "worker": SHARED | STORAGE | PROVIDER,
}
# Secrets whose presence expands compromise blast radius. API needs read/presign R2
# credentials, worker needs write/delete R2 + provider credentials, bot needs neither.
FORBIDDEN={
    "bot": STORAGE | PROVIDER,
    "api": PROVIDER,
    "worker": set(),
}

def present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true", help="fail if high-risk secrets are over-provisioned")
    args=p.parse_args()
    role=os.getenv("SOCIAL_SAVER_ROLE", "all").lower()
    if role not in {"all", *REQUIRED}:
        print("FAIL invalid SOCIAL_SAVER_ROLE (all|bot|api|worker)")
        return 2
    required=(SHARED | STORAGE | PROVIDER) if role == "all" else REQUIRED[role]
    missing=sorted(k for k in required if not present(k))
    if missing:
        print("FAIL missing required environment keys: " + ", ".join(missing))
        return 1
    if role != "all":
        excessive=sorted(k for k in FORBIDDEN[role] if present(k))
        if excessive:
            level="FAIL" if args.strict else "WARN"
            print(f"{level} over-provisioned high-risk keys for role={role}: " + ", ".join(excessive))
            if args.strict:
                return 3
    print(f"PASS staging environment keys present for role={role}")
    missing_optional=sorted(k for k in OPTIONAL if not present(k))
    if missing_optional:
        print("WARN optional keys absent: " + ", ".join(missing_optional))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
