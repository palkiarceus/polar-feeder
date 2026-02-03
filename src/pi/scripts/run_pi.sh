#!/usr/bin/env bash
set -e

# Run from repo root:
#   bash src/pi/scripts/run_pi.sh

CONFIG_PATH="/etc/polar_feeder/config.json"

# Fallback for development if /etc config isn't present
if [ ! -f "$CONFIG_PATH" ]; then
  echo "No /etc config found. Using repo config template."
  CONFIG_PATH="config/config.example.json"
fi

echo "Using config: $CONFIG_PATH"
python3 -m polar_feeder.main --config "$CONFIG_PATH"
