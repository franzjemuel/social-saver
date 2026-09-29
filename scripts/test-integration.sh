#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# A unique Compose project prevents this runner from tearing down other work.
integration_project="social-saver-test-$$"
compose=(docker compose -p "$integration_project" -f tests/integration/compose.yml)
cleanup() { "${compose[@]}" down --volumes --remove-orphans; }
trap cleanup EXIT
"${compose[@]}" up --build --wait --wait-timeout 180
export TEST_DATABASE_URL="postgresql://postgres:local-integration-only@127.0.0.1:${SOCIAL_SAVER_TEST_PORT:-25432}/social_saver_test"
python -m pytest -q tests/integration
