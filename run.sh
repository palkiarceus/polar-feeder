#!/bin/bash
set -e
cd "$(dirname "$0")"

source .venv/bin/activate
export PYTHONPATH="$PWD/src/pi"

python -m polar_feeder.main "$@"
