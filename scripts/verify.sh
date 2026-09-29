#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q .
python -m pytest -q
python - <<'PY'
import ast
from pathlib import Path
for f in Path("apps").rglob("*.py"):
    ast.parse(f.read_text(), filename=str(f))
print("AST verification passed")
PY
docker compose config >/dev/null
echo "Static verification passed"
