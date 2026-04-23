# Polar Feeder (Pi) Project

## Overview

Polar Feeder is an intelligent wildlife feeder controller built for Raspberry Pi (including Pi 5), featuring:

- BLE (Nordic UART Service) remote control interface
- Radar threat detection and safety zoning logic
- RF actuator control via pulse replay (extend/retract)
- Sensor-driven Finite State Machine (IDLE -> LURE -> RETRACT_WAIT -> COOLDOWN)
- Configurable stillness detection and adaptive behavior
- CSV session logging for telemetry and events
- Service-oriented deployment (systemd) and modular architecture

This repository contains the full control path, simulation/demo mode, and utility scripts for development and deployment.

## Goals

1. **Safety-first operation** — arm actuator only when conditions are safe, retract on threats.
2. **Robust control infrastructure** — BLE commands, serial radar data, and GPIO RF actuation.
3. **Observability** — detailed event/telemetry logging, health checks, and status diagnostics.
4. **Extensibility** — config-driven behavior, pluggable radar and actuator settings.
5. **Reproducibility** — clear startup scripts, working examples, and selftest utilities.

## Repository structure

- `src/pi/polar_feeder/` - application code:
  - `main.py` - orchestrator (BLE mode + demo mode)
  - `feeder_fsm.py` - state machine implementation
  - `actuator.py` - high-level actuator API (extend/retract)
  - `radar.py` - serial radar threat reader
  - `transmittingfunc.py` - RF signal emitter
  - `receivingsave.py` - RF signal recorder
  - `ble_interface.py` - Bluetooth command server
  - `config/` - default configs and schema
  - `logging/csv_logger.py` - session logger
- `config/` - repository vendor config templates, schema
- `deploy/systemd/` - service unit file
- `tools/selftest.py` - environment checks (Bluetooth, GPIO, camera, UART)
- `docs/` - generated documentation (codebase, quick ref, config guide)
- `run.sh` - lightweight root launcher
- `src/pi/scripts/run.sh` - robust environment launcher

## Documentation

Please use the generated docs for details:

- `docs/DOCUMENTATION_INDEX.md` - master index (start here)
- `docs/CODEBASE_DOCUMENTATION.md` - architecture and component deep dive
- `docs/CONFIG_GUIDE.md` - config field definitions and examples
- `docs/HARDWARE_ADAPTATION.md` - adapting Polar Feeder to new hardware
- `docs/QUICK_REFERENCE.md` - command cheat sheet + quick commands
- `docs/RPI5_QUICK_START.md` - Pi 5 specific setup/test flow
- `config/RF_SIGNALS_README.md` - RF signal protocol and recording

## Quick start (development)

```bash
cd ~/OneDrive/Desktop/polar-feeder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# app mode
./run.sh --ble-test --config config/config.example.json
# or robust mode
src/pi/scripts/run.sh --ble-test --config config/config.example.json

# verify logs
tail -f logs/*.csv

# selftest utility
python tools/selftest.py
```

## Why do we keep two config paths?

- `config/config.example.json` is the canonical user-facing template.
- `src/pi/polar_feeder/config/config.example.json` is a second copy / package-bound template that stays alongside runtime package imports in this repository structure.
- Keep them in sync (same values). Both are now aligned.

## Deployment (systemd)

```bash
sudo cp deploy/systemd/polar-feeder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable polar-feeder
sudo systemctl start polar-feeder
sudo journalctl -u polar-feeder -f
```

## Validation

- `python tools/selftest.py` for environment checks
- `python - <<'PY'\nfrom polar_feeder.config.loader import load_config\nprint(load_config('config/config.example.json'))\nPY`

## Notes

- Native config is expected at `/etc/polar_feeder/config.json` for deployments.
- Never commit active deployment config.
- This README is now a high-level project overview; detailed configuration and operations are in docs/.

