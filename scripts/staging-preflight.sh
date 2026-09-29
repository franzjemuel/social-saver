#!/usr/bin/env bash
set -euo pipefail
command -v supabase >/dev/null || { echo "Supabase CLI missing"; exit 1; }
command -v railway >/dev/null || { echo "Railway CLI missing"; exit 1; }
python -m compileall -q .
python -m pytest -q
docker build -f Dockerfile.worker -t social-saver-worker:staging .
echo "Local gates passed. Next run: supabase db push --dry-run against STAGING."
