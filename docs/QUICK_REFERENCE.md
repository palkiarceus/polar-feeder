# Quick Reference Guide

## Module Purpose Summary

| Module | Purpose | Key Classes/Functions |
|--------|---------|---------------------|
| **main.py** | Entry point & main loop | `main()`, `make_session_id()` |
| **feeder_fsm.py** | State machine control | `FeederFSM`, `State` enum |
| **actuator.py** | Hardware control interface | `Actuator` class |
| **transmittingfunc.py** | RF signal transmission | `transmit1()`, `transmit2()` |
| **radar.py** | Threat detection | `RadarReader`, `RadarReading` |
| **receivingsave.py** | Signal recording | Standalone utility script |
| **ble_interface.py** | Bluetooth communication | `BleServer`, `BleCommand` |
| **config/loader.py** | Configuration loading | `load_config()` |
| **logging/csv_logger.py** | Event logging | `CsvSessionLogger` |

## FSM State Diagram

```
                    ┌─────────────────┐
                    │      IDLE       │ ← START HERE
                    └────────┬────────┘
                             │
                  [enabled & not threat]
                             │
                    ┌────────▼────────┐
                    │      LURE       │ ARM EXTENDED
                    └────────┬────────┘
                             │
                  [threat detected]
                             │
                    ┌────────▼─────────────────┐
                    │   RETRACT_WAIT          │ HOLD ~2.5s
                    │  (allow animal to grab) │
                    └────────┬─────────────────┘
                             │
                  [delay expired]
                             │
                    ┌────────▼─────────────────┐
                    │   COOLDOWN              │ ARM RETRACTING
                    │  (prevent rapid cycle)  │ HOLD ~2.0s
                    └────────┬─────────────────┘
                             │
                  [cooldown expired]
                             │
                    [disabled: any state]
                             │
                    ┌────────▼────────┐
                    │      IDLE       │ ← SAFE STATE
                    └─────────────────┘
```

## Command Quick Reference

### Enable/Disable
```
ENABLE=1    # Enable feeder
ENABLE=0    # Disable feeder (safe)
```

### Manual Control
```
ACTUATOR=EXTEND     # Manually extend arm
ACTUATOR=RETRACT    # Manually retract arm
```

### Parameters (Runtime Only)
```
SET retract_delay_ms=2000    # Change extend hold time
SET radar_enabled=0          # Disable radar
SET <key>=<value>            # Set any parameter
```

### Status
```
STATUS          # Get current status
STATUS RADAR    # Get radar reading
GET <key>       # Get parameter value
```

## Configuration Quick Reference

```json
{
  "stillness": {
    "trigger_threshold": 0.85,    // 0-1, higher = harder to trigger
    "min_duration_s": 2.0         // Seconds of stillness needed
  },
  "actuator": {
    "retract_delay_ms": 2500,     // 0-3000ms, food dispense time
    "pulse_ms": 200               // RF signal duration (usually 100-200)
  },
  "radar": {
    "enabled": true,              // true/false
    "distance_jump_m": 0.20       // Threat sensitivity (0.1-0.5)
  }
}
```

## Running the System

### BLE Test Mode (Full Control)
```bash
python src/pi/polar_feeder/main.py --ble-test
```
Features: Remote control, logging, radar, threat response

### Demo Mode (No Hardware)
```bash
python src/pi/polar_feeder/main.py --demo-seconds 60
```
Features: Simulated data, CSV logging only

### Custom Config
```bash
python src/pi/polar_feeder/main.py --config config/my-config.json --ble-test
```

## File Locations

| Purpose | Location |
|---------|----------|
| Main code | `src/pi/polar_feeder/` |
| Configuration | `config/config.example.json` |
| RF signals | `config/rf_signal1.json`, `rf_signal2.json` |
| Logs | `logs/session_*.csv` |
| Documentation | `*.md` files in root |

## Typical Debug Output

```
[PATH] polar_feeder pkg: .../src/pi/polar_feeder/__init__.py
[BLE] Publishing Now
[RADAR] started on /dev/ttyAMA0
BLE test mode running. Send newline-terminated commands (end with \n).
Session log: logs/session_20260326T143022Z_a1b2c3d4.csv
[BLE WRITE] chunk: 'ENABLE=1\n' ...
[DEBUG] handle_ble raw='ENABLE=1'
[FSM] IDLE -> LURE
[FSM] LURE -> RETRACT_WAIT
[FSM] RETRACT_WAIT -> COOLDOWN
[FSM] COOLDOWN -> IDLE
```

## Performance Targets

| Metric | Target |
|--------|--------|
| Main loop frequency | 10Hz (100ms) |
| BLE response time | <50ms |
| Radar update rate | 10-30Hz |
| FSM state transition time | <5ms |
| CSV write delay | <1 second |
| Total power consumption | <2W idle, <5W active |

## Key Files to Edit

| Task | File |
|------|------|
| Change feeder behavior | `config/config.example.json` |
| Add new BLE command | `main.py` (handle_ble function) |
| Change FSM logic | `feeder_fsm.py` (tick method) |
| Record new RF signal | `receivingsave.py` (run it) |
| Add new sensor | Create new module in `src/pi/polar_feeder/` |

## Common Issues & Fixes

| Issue | Solution |
|-------|----------|
| Feeder won't extend | Check rf_signal1.json exists and GPIO17 accessible |
| BLE not working | Run with `--ble-test` flag |
| Commands not responding | Check command format has newline at end |
| Radar not detecting threats | Lower `distance_jump_m` in config (more sensitive) |
| Feeder keeps retracting | Increase `retract_delay_ms` in config |
| High CPU usage | Increase main loop sleep time (reduce Hz) |

## Testing Commands (Via BLE)

```bash
# Start BLE test mode
python src/pi/polar_feeder/main.py --ble-test

# In another terminal, connect and test:
# (Assuming BLE device is discoverable as "PolarFeeder")

# Use bluetoothctl or a BLE client app to send:
ENABLE=1        # Should extend arm
ENABLE=0        # Should retract and idle
ACTUATOR=EXTEND # Manual extend
ACTUATOR=RETRACT # Manual retract
STATUS          # Get current state
GET enable      # Get parameter value
```

## Architecture Layers

```
┌──────────────────────────────┐
│  User/Mobile App via BLE     │
├──────────────────────────────┤
│  BLE Interface (Bluetooth)   │  ← ble_interface.py
├──────────────────────────────┤
│  Main Loop Control           │  ← main.py
│  + Command Handler           │
├──────────────────────────────┤
│  FeederFSM State Machine     │  ← feeder_fsm.py
├──────────────────────────────┤
│  Sensors:                    │  ← radar.py
│  - Radar threat detection   │
│  - Motion detection (config) │
├──────────────────────────────┤
│  Actuator Control            │  ← actuator.py
├──────────────────────────────┤
│  RF Transmission             │  ← transmittingfunc.py
├──────────────────────────────┤
│  GPIO Hardware               │  ← lgpio library
│  (Raspberry Pi)              │
└──────────────────────────────┘
```

## Data Types

### BleCommand
```python
BleCommand(raw="ENABLE=1")
```

### RadarReading
```python
RadarReading(
    bin_index=5,           # Detection zone
    distance_m=2.34,       # Distance
    threat=False,          # Sudden change?
    valid=True             # Parse success
)
```

### FSM State
```python
State.IDLE          # Arm retracted
State.LURE          # Arm extended
State.RETRACT_WAIT  # Holding for grab
State.COOLDOWN      # Preventing rapid cycle
```

## Environment Variables (Optional)

```bash
# Set serial port for radar (overrides config)
export RADAR_PORT=/dev/ttyUSB0

# Enable verbose logging
export VERBOSE=1

# Set config path
export FEEDER_CONFIG=/path/to/config.json
```

---

**Save this file for quick reference during development!**
