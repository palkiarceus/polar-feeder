#!/bin/bash
# Polar Feeder startup wrapper. Intended for local dev and service startup.
# See deploy/systemd/polar-feeder.service for production behavior.

set -e
# Exit immediately if a command exits with non-zero status
# This prevents partial initialization or hidden failures.

cd "$(dirname "$0")"
# Switch to the script directory (project root), so relative paths are deterministic.

# Activate virtual environment with same Python deps used for development.
source .venv/bin/activate

# Ensure Python can import the internal package from src/pi.
export PYTHONPATH="$PWD/src/pi"

# Start the main application module, forwarding all passed arguments.
# Example: ./run.sh --ble-test --config config/config.example.json
python -m polar_feeder.main "$@"
