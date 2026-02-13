#!/bin/bash
set -e

# Repo root = two levels up from this script: src/pi/scripts -> repo
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

source .venv/bin/activate
export PYTHONPATH="$REPO_ROOT/src/pi"

python -m polar_feeder.main "$@"
