#!/usr/bin/env bash
# Build the same non-editable package used in containers. Import outside the
# checkout so pytest's pythonpath setting cannot conceal missing wheel modules.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
package_tmp="$(mktemp -d)"
trap 'rm -rf "$package_tmp"' EXIT
python -m pip wheel --no-deps --wheel-dir "$package_tmp/wheels" "$repo_root"
python -m venv "$package_tmp/venv"
"$package_tmp/venv/bin/python" -m pip install "$package_tmp"/wheels/*.whl
cd "$package_tmp"
TELEGRAM_BOT_TOKEN=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
"$package_tmp/venv/bin/python" -I - <<'PY'
from pathlib import Path
import sys
import importlib
for name in ('apps.bot.main', 'apps.api.main', 'apps.worker.main', 'ops.staging_canary'):
    module = importlib.import_module(name)
    assert Path(module.__file__).is_relative_to(Path(sys.prefix)), name
print('Installed wheel service imports passed')
PY
