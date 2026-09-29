#!/usr/bin/env bash
set -euo pipefail
python -m ops.staging_canary "$@"
