#!/usr/bin/env bash
set -euo pipefail

# Repo root = src/pi/scripts -> repo
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: venv python not found at $VENV_PY" >&2
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export PYTHONPATH="$REPO_ROOT/src/pi"
export PYTHONUNBUFFERED=1

exec "$VENV_PY" -m polar_feeder.main "$@"