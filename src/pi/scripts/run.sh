#!/usr/bin/env bash
# Robust startup script placed at src/pi/scripts/run.sh. This is the preferred
# entrypoint for systemd or manual near-root execution in the repo tree.

set -euo pipefail
# -e: exit on first error
# -u: treat unset vars as error
# -o pipefail: fail when any pipe stage fails

# Repo root = src/pi/scripts -> ../../.. from this path
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: venv python not found at $VENV_PY" >&2
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Ensure module import path resolves to local project source
export PYTHONPATH="$REPO_ROOT/src/pi"
export PYTHONUNBUFFERED=1

# Launch main Polar Feeder module with all arguments passed through
exec "$VENV_PY" -m polar_feeder.main "$@"