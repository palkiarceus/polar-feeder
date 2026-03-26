# Polar Feeder Raspberry Pi 5 Quick Start / Validation Guide

This document captures the complete instructions from the previous summary, with exact usage commands and test flows for Raspberry Pi 5 in a Python virtual environment.

## 1) Purpose

- Rapidly verify environment and hardware readiness for Polar Feeder on Raspberry Pi 5
- Explain all `run.sh` script variants and why both exist
- Explain dependency on `lgpio` and how to install if needed
- Provide comprehensive command list for testing modes and features

## 2) `run.sh` variants (why both exist)

### 2.1 Root-level `run.sh`

- Location: `./run.sh` (repo root)
- Purpose: quick local dev execution
- Behavior:
  - `set -e`
  - `cd` to project root
  - activate `.venv`
  - set `PYTHONPATH=$PWD/src/pi`
  - run `python -m polar_feeder.main "$@"`
- Use when you want a compact launcher for manual tests.

### 2.2 `src/pi/scripts/run.sh`

- Location: `src/pi/scripts/run.sh`
- Purpose: recommended service startup (systemd, robust production run)
- Behavior:
  - `set -euo pipefail` (strict)
  - compute `REPO_ROOT` reliably
  - check `VENV_PY` exists and is executable
  - sets `PYTHONPATH=REPO_ROOT/src/pi`
  - sets `PYTHONUNBUFFERED=1`
  - execs `VENV_PY -m polar_feeder.main "$@"`
- Use for systemd-run, automation, and non-interactive deployment.

### 2.3 systemd service launcher

- file: `deploy/systemd/polar-feeder.service`
- calls `src/pi/scripts/run.sh --ble-test --config /etc/polar_feeder/config.json`
- includes pre-start GPIO init and restarts on failure.

## 3) `lgpio` dependency

- `lgpio` is the GPIO library used by:
  - `src/pi/polar_feeder/transmittingfunc.py`
  - `src/pi/polar_feeder/receivingsave.py`
- Typical install on Raspbian/RPi OS:
  - `sudo apt install python3-lgpio` or `pip install lgpio`
- The repository may include a `lgpio/` directory as a placeholder or vendored module.
- If it’s empty, the runtime will use installed system package.

## 4) Documentation coverage (files created in the project)

- `DOCUMENTATION_INDEX.md` - master navigation
- `CODEBASE_DOCUMENTATION.md` - complete architecture and module details
- `CONFIG_GUIDE.md` - config keys/ranges and examples
- `QUICK_REFERENCE.md` - fast lookup table and command cheat sheet
- `RF_SIGNALS_README.md` - RF recording/transmit format details
- `DOCUMENTATION_SUMMARY.md` - status and checklist metrics
- `README_DOCUMENTATION.md` - final completion summary
- `docs/RPI5_QUICK_START.md` - this file (Pi-specific quick start)

## 5) Setup and run commands (Raspberry Pi 5, terminal, virtualenv)

### 5.1 Create virtual environment

```bash
cd ~/OneDrive/Desktop/polar-feeder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5.2 Run app (local dev)

```bash
./run.sh --ble-test --config config/config.example.json
```

### 5.3 Run app (robust script)

```bash
src/pi/scripts/run.sh --ble-test --config config/config.example.json
```

### 5.4 Run as systemd service

```bash
sudo cp deploy/systemd/polar-feeder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable polar-feeder
sudo systemctl start polar-feeder
sudo journalctl -u polar-feeder -f
```

### 5.5 Run self-test utility

```bash
python tools/selftest.py
```

### 5.6 Verify configuration loader (quick check)

```bash
python - <<'PY'
from polar_feeder.config.loader import load_config
cfg = load_config('config/config.example.json')
print(cfg)
PY
```

### 5.7 Inspect logs

```bash
tail -f logs/*.csv
``` 

## 6) Specific testing commands for each feature

### 6.1 BLE command test (via motorcycle / mobile app or BLE tool)

Common command examples:
- `enable`
- `disable`
- `standby`
- `lure`
- `retract`
- `status`
- `health`
- `version`

Expected: Main logs show event rows and telemetry rows in CSV.

### 6.2 Radar sensor test

- Ensure `radar.enabled=true` in config
- Ensure radar sensor connected on serial port configured in `radar.port`
- Confirm `/dev/serial0` (or configured port) exists.
- Check `tools/selftest.py` output for serial node list.

### 6.3 Actuator RF test

- enable mode
- send `extend` then `retract` commands
- confirm `transmittingfunc` executed and `logs/*.csv` records events.

### 6.4 Safety test

- `ble_disconnect_safe_idle: true` in config
- Mo simulate BLE disconnect and see state change to IDLE via logs

## 7) Where to read each existing doc quickly

- If you need architecture + flow: `CODEBASE_DOCUMENTATION.md`.
- If you need config parameters: `CONFIG_GUIDE.md`.
- If you need quick commands / FSM and behavior: `QUICK_REFERENCE.md`.
- If you need RF signal details: `RF_SIGNALS_README.md`.
- If you need verification status and coverage: `DOCUMENTATION_SUMMARY.md`.
- If you need overall index & next steps: `DOCUMENTATION_INDEX.md`.

## 8) Custom “Pi 5 interactive checklist”

1. Confirm dependencies:
   - `python3`, `pip`, `venv`, `lgpio`, `bluez`, `systemd`, `libcamera`, `v4l-utils`
2. Confirm hardware revisions:
   - Radar connected at the correct UART port
   - RF transmitter pins wired to gpio17/gpio27 (or adjust in code)
3. Confirm service user has needed permissions:
   - sudo user or `arcticproject` has access to `/dev/gpiomem`, `/dev/tty*`, `bluetooth`
4. Run `tools/selftest.py`, fix missing pieces.
5. Run with `src/pi/scripts/run.sh` and inspect `journalctl` or `tail -f logs/*.csv`.
6. Send BLE commands and validate state machine transitions.

---

# Cleaner Copy for quick reference at shell

```bash
cd ~/OneDrive/Desktop/polar-feeder
source .venv/bin/activate
./run.sh --ble-test --config config/config.example.json
# OR robust method
src/pi/scripts/run.sh --ble-test --config config/config.example.json
# watch logs
tail -f logs/*.csv
# self-test
python tools/selftest.py
# config parse sanity
python - <<'PY'
from polar_feeder.config.loader import load_config
print(load_config('config/config.example.json'))
PY
```
