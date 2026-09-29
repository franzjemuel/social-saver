#!/usr/bin/env bash
set -euo pipefail
command -v supabase >/dev/null || { echo "Install Supabase CLI first."; exit 1; }
command -v docker >/dev/null || { echo "Docker-compatible runtime required."; exit 1; }
[ -f supabase/config.toml ] || supabase init
supabase start
supabase db reset
echo
echo "Copy the local DB URL shown by 'supabase status' into DATABASE_URL in .env."
echo "Then run: docker compose up --build -d worker"
echo "Then run: docker compose run --rm worker python ops/integration_smoke.py"
