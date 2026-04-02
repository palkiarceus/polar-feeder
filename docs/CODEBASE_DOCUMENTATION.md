# Polar Feeder - Comprehensive Code Documentation

## Project Overview

The Polar Feeder is an intelligent food dispensing system that uses multiple sensors and actuators to safely feed wildlife while protecting food from predators. The system combines:

- **BLE (Bluetooth Low Energy)** - Remote control and monitoring
- **Motion Detection** - Triggers feeding when animals are present
- **Radar Sensing** - Detects threats (approaching predators)
- **RF Actuators** - Remote-controlled mechanical arm
- **CSV Logging** - Records all events for analysis
- **Finite State Machine** - Ensures safe operation sequences

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     POLAR FEEDER SYSTEM                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  User / Mobile App ──────────────── BLE Interface           │
│         ↓                                   ↓                │
│    Commands (ENABLE=1, etc.) ─→ BleServer (Rx/Tx Chars)   │
│                                            ↓                │
│                                ┌────────────────────┐       │
│                                │    Main Loop       │       │
│                                │  (main.py)         │       │
│                                └────────────────────┘       │
│                                     ↓    ↑                  │
│    Sensors: Motion/Radar ──→ FeederFSM │                  │
│            (radar.py)         │        │                  │
│                               │        │ Control Signals  │
│                               ↓        │                  │
│                         Actuator (RF Transmitter)         │
│                         (transmittingfunc.py)             │
│                               ↓                           │
│                         Mechanical Arm ←──RF Signal       │
│                      (Extend/Retract)   (rf_signal*.json) │
│                                                             │
│    CSV Logger ←─ All Events & Telemetry                   │
│    (csv_logger.py)                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Module Structure

### Core Modules

#### 1. **main.py** - Entry Point
**Purpose:** Orchestrates the entire system, handles two modes of operation

**Key Functions:**
- `main()`: Primary entry point, parses arguments and coordinates all subsystems
- `make_session_id()`: Generates unique session identifiers

**Modes:**
- **BLE Test Mode** (`--ble-test`): Full interactive control via Bluetooth
  - Starts BLE server for remote commands
  - Runs FSM control loop
  - Enables radar threat detection
  - Logs all events and telemetry
  
- **Demo Mode** (default): Simulated logging without hardware
  - Generates fake sensor data
  - Demonstrates CSV logging
  - No actual actuator control

**Key Features:**
- BLE command handler with 20+ supported commands
- Real-time FSM state monitoring
- Safety timeout (disables feeder if BLE disconnects)
- Radar threat detection with configurable arming delay

---

#### 2. **feeder_fsm.py** - Finite State Machine
**Purpose:** Controls feeder behavior through well-defined states and transitions

**States:**
- **IDLE**: Arm retracted, waiting for enable signal
- **LURE**: Arm extended, waiting for threat detection
- **RETRACT_WAIT**: Holding extended position (allows animal to grab food)
- **COOLDOWN**: Arm retracted, preventing rapid cycles

**Key Methods:**
- `tick(enable, threat)`: Update FSM state (call regularly, e.g., every 100ms)
  - Takes current enable signal and threat status
  - Updates actuator if needed
  - Manages all timing/deadlines

**Safety Features:**
- Immediate retraction if disabled
- Threat-triggered response with configurable delay
- Cooldown prevents mechanism damage
- Time-based state transitions (deadline system)

**Example Cycle:**
1. IDLE (disabled) → IDLE (wait for enable)
2. IDLE → LURE (enabled, arm extends)
3. LURE → RETRACT_WAIT (threat detected, wait 2.5s)
4. RETRACT_WAIT → COOLDOWN (retract arm)
5. COOLDOWN → IDLE (wait 2s, then ready for next)

---

#### 3. **actuator.py** - Hardware Interface
**Purpose:** Abstract interface for RF actuator control

**Key Methods:**
- `open()`: Initialize GPIO resources (currently no-op)
- `close()`: Cleanup resources (currently no-op)
- `extend()`: Send RF signal to extend arm
- `retract()`: Send RF signal to retract arm
- `extend_then_retract(delay_s)`: Complete dispense cycle

**Implementation:**
- Delegates to RF transmission functions
- Stateless (no persistent GPIO connections needed)
- Clean API for FSM to use

---

#### 4. **transmittingfunc.py** - RF Signal Transmission
**Purpose:** Replay recorded RF pulse patterns to control actuators

**How It Works:**
1. Load pre-recorded pulse data from JSON (states and durations)
2. Open GPIO chip and claim output pin
3. Iterate through pulse sequence:
   - Set GPIO to state (0 or 1)
   - Sleep for duration
4. End with GPIO low (safety)

**Key Functions:**
- `_load(filename)`: Load RF signal from JSON file
- `_transmit(filename)`: Transmit RF signal on GPIO17
- `transmit1()`: Send EXTEND command
- `transmit2()`: Send RETRACT command
- `transmitwithdelay(delay_s)`: Complete dispense cycle

**Signal File Format:**
```json
{
  "states": [0, 1, 0, 1, ...],      // GPIO levels
  "durations": [0.001, 0.002, ...]  // Seconds per state
}
```

---

#### 5. **radar.py** - Motion & Threat Detection
**Purpose:** Read RF radar sensor data via serial, detect approaching threats

**Key Classes:**
- `RadarReading`: Data class for single measurement
  - `bin_index`: Detection zone
  - `distance_m`: Distance to target
  - `threat`: Boolean (sudden distance change)
  - `valid`: Parse success
  
- `RadarReader`: Thread-based serial reader
  - Runs background thread for non-blocking reads
  - Thread-safe access via `get_latest()`
  - Automatic threat detection (distance jump)

**How Threat Detection Works:**
1. Read radar measurements continuously
2. Calculate delta from previous distance
3. If delta ≥ `distance_jump_m` threshold → mark as threat
4. FSM responds immediately to threat signal

**Thread Safety:**
- Uses internal lock for data access
- Returns copies to avoid race conditions
- Safe to call from any thread

---

#### 6. **receivingsave.py** - RF Signal Recording Utility
**Purpose:** Record RF signals from remote controls for later replay

**How to Use:**
1. Run the script: `python receivingsave.py`
2. Connect RF receiver to GPIO27
3. Press button on remote when prompted
4. Script records 3 seconds of RF pulses
5. Saves to `rf_signal2.json`

**Recording Process:**
- Detects GPIO state changes
- Measures pulse durations with ~5µs resolution
- Stores as JSON with states and durations arrays
- Can be replayed by transmittingfunc.py

---

#### 7. **ble_interface.py** - Bluetooth Communication
**Purpose:** BLE GATT server for remote control and status queries

**Nordic UART Service (NUS):**
- Standard BLE profile for serial-like communication
- RX Characteristic: Receive commands from client
- TX Characteristic: Send responses to client

**Protocol:**
- Newline-delimited commands and responses
- Each command returns ACK or ERR response
- Example: `ENABLE=1\n` → `ACK ENABLE=1\n`

**Key Features:**
- Message buffering for incomplete lines
- Escape sequence normalization
- Version-flexible notify implementation
- Support for both notifications and polling
- Last RX timestamp tracking (for safety timeout)

---

### Supporting Modules

#### config/loader.py
- Loads and validates JSON configuration
- Type checking and range validation
- Raises errors if config invalid

#### logging/csv_logger.py
- Records events and telemetry to CSV
- Thread-safe logging
- Automatic session management

---

## Configuration System

**Files:**
- `config/config.example.json` - Main configuration
- `config/schema.json` - JSON schema for validation
- `CONFIG_GUIDE.md` - Detailed explanation of settings

**Main Sections:**
1. **stillness** - Motion detection thresholds
2. **logging** - CSV recording parameters
3. **radar** - Sensor configuration
4. **safety** - Safety features
5. **actuator** - Timing parameters

**Example:**
```python
cfg = load_config("config/config.example.json")
retract_delay = cfg.actuator.retract_delay_ms  # 2500 (ms)
```

---

## BLE Command Reference

### Enable/Disable
- `ENABLE=0` - Disable feeder (safe idle)
- `ENABLE=1` - Enable feeder (arm extends if enabled)
- Response: `ACK ENABLE=<0|1>`

### Manual Retraction
- `RETRACT` - Manually retract arm from FEEDING state
- Response: `ACK RETRACT` or `ERR RETRACT not_in_feeding_state`

### Vision Detection Input
- `VISION=<detection_line>` - Send YOLO detection data
- Format: `VISION=id,timestamp,ymin,ymax,xmin,xmax`
- Example: `VISION=1,1690001123.45,50,200,30,180`
- Response: `ACK VISION motion=<magnitude>`

### Parameter Configuration

**User-Configurable Parameters (Three Main Control Variables):**

1. **[DELAY]** `retract_delay_ms` - Delay from threat detection to retraction
   - `SET retract_delay_ms=<0-3000>` - Range: 0-3000 milliseconds
   - Example: `SET retract_delay_ms=1500` - 1.5 second delay
   - Response: `ACK SET retract_delay_ms=<value>`

2. **[STILLNESS]** `motion_threshold` - Movement tolerance before threat
   - `SET motion_threshold=<0-1000>` - Range: 0-1000 pixels
   - Example: `SET motion_threshold=15` - Allow 15 pixel movement
   - Response: `ACK SET motion_threshold=<value>`
   - Higher value = more movement allowed before triggering threat

3. **[DISTANCE]** `detection_distance_m` - Distance to start the "game"
   - Configuration only (requires JSON edit or config reload)
   - Range: 0.5-50.0 meters
   - If bear is beyond this distance, threat signals are ignored

**Generic Parameter Set:**
- `SET <key>=<value>` - Set any runtime parameter
- Response: `ACK SET <key>=<value>`
- Use `STATUS` to see all current parameter values

### Status Queries
- `STATUS` - Get current feeder status
- `STATUS RADAR` - Get latest radar reading
- Response: `OK <status_line>`

### Error Responses
- `ERR EMPTY` - Empty command
- `ERR BAD_VALUE <param>` - Invalid parameter value
- `ERR OUT_OF_RANGE <param> <min> <max>` - Value outside valid range
- `ERR UNKNOWN_COMMAND` - Command not recognized

---

## Data Flow Example

### Typical Session Flow (BLE Test Mode)

```
1. [Client] ENABLE=1
2. [Server] ACK ENABLE=1
   → main.py: Set runtime["enable"] = 1
   → FSM: IDLE → LURE (arm extends via transmit1)
   
3. [Radar] Detects motion
   → FSM: LURE → RETRACT_WAIT
   
4. [Timer] retract_delay_ms expires (e.g., 2500ms)
   → FSM: RETRACT_WAIT → COOLDOWN (arm retracts via transmit2)
   
5. [Timer] cooldown_s expires (e.g., 2.0s)
   → FSM: COOLDOWN → IDLE (ready for next)
   
6. [Logging] All events recorded to CSV:
   - ENABLE event
   - THREAT detected event
   - ACTUATOR extend/retract commands
   - FSM state transitions
   
7. [Client] ENABLE=0
8. [Server] ACK ENABLE=0
   → FSM: [Any state] → IDLE (arm retracts if not already)
```

---

## File Organization

```
polar-feeder/
├── config/
│   ├── config.example.json     # Main configuration
│   ├── schema.json             # Configuration validation
│   ├── rf_signal1.json         # EXTEND RF signal
│   └── rf_signal2.json         # RETRACT RF signal
│
├── src/pi/polar_feeder/
│   ├── main.py                 # Entry point & main loop
│   ├── feeder_fsm.py           # State machine
│   ├── actuator.py             # Actuator control interface
│   ├── transmittingfunc.py     # RF signal transmission
│   ├── receivingsave.py        # RF signal recording utility
│   ├── radar.py                # Radar sensor reading
│   ├── ble_interface.py        # BLE GATT server
│   ├── __init__.py
│   │
│   ├── config/
│   │   ├── loader.py           # Config loading & validation
│   │   └── [config files]
│   │
│   └── logging/
│       └── csv_logger.py       # CSV event/telemetry logging
│
├── logs/
│   └── [session CSV files]
│
├── docs/
│   └── [documentation]
│
├── CONFIG_GUIDE.md             # Configuration documentation
└── README.md                   # This file
```

---

## Usage Examples

### Run BLE Test Mode (Full Control)
```bash
python src/pi/polar_feeder/main.py --ble-test --config config/config.example.json
```

### Run Demo Mode (No Hardware Needed)
```bash
python src/pi/polar_feeder/main.py --demo-seconds 30
```

### Record New RF Signal
```bash
python src/pi/polar_feeder/receivingsave.py
# [Follow prompts, press remote button]
# Result: rf_signal2.json
```

### Python Integration
```python
from polar_feeder.feeder_fsm import FeederFSM, State
from polar_feeder.actuator import Actuator
from polar_feeder.config.loader import load_config

# Load config
cfg = load_config("config/config.example.json")

# Create actuator and FSM
actuator = Actuator()
actuator.open()
fsm = FeederFSM(actuator, cfg.actuator.retract_delay_ms)

# Run control loop
threat_detected = False
while True:
    fsm.tick(enable=True, threat=threat_detected)
    # ... check sensors, update threat_detected ...
    time.sleep(0.1)

actuator.close()
```

---

## Key Design Patterns

### 1. **Finite State Machine Pattern**
- Clear, safe state transitions
- Each state knows valid next states
- Reduces complexity and bugs

### 2. **Thread-Safe Async I/O**
- Background threads for serial/BLE I/O
- Locks protect shared data
- Main loop never blocks on I/O

### 3. **Configuration-Driven Behavior**
- All parameters in JSON config
- No magic numbers in code
- Easy to tune without code changes

### 4. **Layered Architecture**
- Low-level: GPIO, serial I/O
- Mid-level: Sensor readers, actuators
- High-level: FSM, main control loop
- Presentation: BLE, logging

### 5. **Fail-Safe Design**
- Disabled → immediate retraction
- BLE timeout → force idle
- Threat detected → immediate retraction
- Cooldown prevents mechanism damage

---

## Troubleshooting Guide

### Issue: Feeder Won't Extend
**Possible Causes:**
1. RF signal file missing (rf_signal1.json)
2. GPIO17 not accessible (permissions)
3. Actuator initialization failed
4. FSM not in IDLE state

**Debug:**
```bash
python -c "from polar_feeder.transmittingfunc import transmit1; transmit1()"
```

### Issue: BLE Commands Not Working
**Possible Causes:**
1. BLE server not started (use `--ble-test`)
2. Client not connected
3. Command format incorrect
4. Command handler exception

**Debug:**
- Check stdout for debug messages (prefixed with `[DEBUG]`)
- Ensure newline at end of command
- Verify command format matches specification

### Issue: Radar Threat Detection Not Working
**Possible Causes:**
1. Radar not enabled in config
2. Serial port incorrect
3. Radar sensor not powered
4. FSM not in LURE state when tested

**Debug:**
```bash
cat /dev/ttyAMA0 | grep "bin="  # Should see radar output
```

---

## Safety Considerations

1. **Always test with feeder disabled** - Prevent injury during development
2. **RF signal specificity** - Signals only work with matching receiver
3. **Cooldown period** - Prevents mechanism damage from rapid cycling
4. **BLE timeout safety** - Disables feeder if connection lost
5. **Threat response** - Retracts immediately if predator detected

---

## Performance Considerations

- **Main loop frequency**: 10Hz recommended (0.1s sleep)
- **Telemetry sampling**: 5Hz for typical monitoring
- **BLE timeout**: 30 seconds default
- **Radar arming delay**: 1.5 seconds after enable
- **Retract delay range**: 0-3000ms (configurable)
- **Cooldown period**: 2 seconds (tunable)

---

## Future Enhancements

1. **Real-time video analysis** - Computer vision threat detection
2. **Weather-based scheduling** - Disable in rain, etc.
3. **Multi-feeder coordination** - Synchronize multiple devices
4. **Advanced telemetry** - Battery voltage, temperature, etc.
5. **Machine learning** - Predict animal behavior
6. **Web dashboard** - Remote monitoring/control
7. **Automatic updates** - OTA firmware updates

---

**Last Updated:** March 26, 2026
**Version:** 1.0
**Status:** Well-documented and ready for deployment
