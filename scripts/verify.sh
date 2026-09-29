#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q apps core providers ops scripts tests
python -m pytest -q
python - <<'PY'
import ast
from pathlib import Path
for f in Path("apps").rglob("*.py"):
    ast.parse(f.read_text(), filename=str(f))
print("AST verification passed")
PY
python scripts/staging-readiness.py
SOCIAL_SAVER_ENV_FILE=.env.example docker compose --env-file .env.example config >/dev/null
echo "Static verification passed"
