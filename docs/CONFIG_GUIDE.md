# Configuration Guide for Polar Feeder

## Overview

The Polar Feeder uses a JSON configuration file to control all aspects of operation. This guide explains each setting.

## Configuration File Structure

The configuration is organized into 5 main sections:

### 1. Stillness (Motion Detection)

```json
"stillness": {
  "publish_hz": 5,           // How often to publish stillness data (times per second)
  "trigger_threshold": 0.85, // Stillness level (0-1) that triggers feeder
  "min_duration_s": 2.0      // How long animal must be still before feeder activates
}
```

**What it does:**
- Monitors for periods of stillness (animal not moving)
- When stillness value exceeds threshold AND lasts for min_duration_s, feeder is enabled
- Higher threshold = animal must be more still to trigger

**Typical values:**
- `trigger_threshold`: 0.75-0.90 (0.85 is moderate)
- `min_duration_s`: 1.0-3.0 seconds

### 2. Logging (CSV Event/Telemetry Recording)

```json
"logging": {
  "enabled": true,          // Enable/disable CSV logging
  "telemetry_hz": 5,        // How often to save sensor readings (times per second)
  "max_storage_mb": 500,    // Maximum disk space for logs before wrapping
  "log_dir": "logs"         // Directory to save CSV session files
}
```

**What it does:**
- Records all feeder events (enable, disable, actuator commands)
- Samples sensor data at regular intervals
- Saves to CSV files for analysis
- Each session gets a unique timestamped filename

**Typical values:**
- `telemetry_hz`: 5-10 (5 is good balance of detail vs storage)
- `max_storage_mb`: 100-1000 (depends on available storage)

### 3. Radar (Motion Sensing for Threat Detection)

```json
"radar": {
  "enabled": true,              // Enable/disable radar sensor
  "port": "/dev/ttyAMA0",       // Serial port for radar (RPi UART)
  "baud": 115200,               // Serial communication speed
  "timeout_s": 0.1,             // Read timeout
  "zone_m": [1.0, 2.0, 3.0],   // Distance zones to monitor (in meters)
  "distance_jump_m": 0.20       // Sudden distance change threshold for threat
}
```

**What it does:**
- Monitors for approaching animals
- Detects sudden distance changes (animal rushing in)
- Triggers immediate retraction if threat detected
- Prevents predators from stealing food

**Port Configuration:**
- Raspberry Pi: `/dev/ttyAMA0` (GPIO 14/15)
- USB adapter: `/dev/ttyUSB0` or similar
- Windows: `COM3`, `COM4`, etc.

**Typical values:**
- `distance_jump_m`: 0.10-0.50m (0.20 is moderate sensitivity)
- `zone_m`: [1.0, 2.0, 3.0] for short-medium-long range detection

### 4. Safety (Security Features)

```json
"safety": {
  "ble_disconnect_safe_idle": true  // Force IDLE if BLE connection lost
}
```

**What it does:**
- `true`: Feeder disables if controller loses BLE connection (safe)
- `false`: Feeder continues operating based on last command (risky)

**Recommendation:** Keep as `true` for unattended operation

### 5. Actuator (Motor/Relay Timing)

```json
"actuator": {
  "retract_delay_ms": 2500,  // Time to hold arm extended (milliseconds)
  "pulse_ms": 200            // RF signal pulse duration
}
```

**What it does:**
- Controls how long arm stays extended for food dispensing
- Longer delay = more food given
- Controls RF signal transmission parameters

**retract_delay_ms:**
- Range: 0-3000ms (0-3 seconds)
- 500-1000ms: Small food portions
- 1500-2500ms: Medium portions
- 2500-3000ms: Large portions

**pulse_ms:**
- Range: 50-1000ms
- Controls RF signal shape (usually 100-200ms)
- Shouldn't need to change unless RF hardware changes

## Example Configurations

### Aggressive Feeding (Max food, quick)
```json
"retract_delay_ms": 3000,  // Full 3 seconds
"min_duration_s": 0.5,     // Trigger quickly
"trigger_threshold": 0.70  // Lower threshold = easier to trigger
```

### Conservative Feeding (Min food, careful)
```json
"retract_delay_ms": 500,   // Half second only
"min_duration_s": 5.0,     // Wait for prolonged stillness
"trigger_threshold": 0.95  // Very high threshold needed
```

### Nocturnal Only (Night sensing)
```json
"publish_hz": 2,           // Reduce overhead
"telemetry_hz": 2,         // Sample less frequently
"distance_jump_m": 0.10    // Sensitive radar for night detection
```

## Validation

The config is validated against [schema.json](schema.json) which enforces:
- Type checking (string, number, boolean)
- Range validation (min/max values)
- Required fields
- Array structure

## Loading Configuration

Python code to load config:
```python
from polar_feeder.config.loader import load_config

cfg = load_config("config/config.example.json")
print(cfg.actuator.retract_delay_ms)  # Access nested values
```

## Troubleshooting

**Feeder not triggering:**
- Lower `trigger_threshold`
- Reduce `min_duration_s`
- Check `stillness` sensor connection

**Feeder triggering too easily:**
- Raise `trigger_threshold`
- Increase `min_duration_s`

**Food not dispensing:**
- Increase `retract_delay_ms`
- Check RF signal files (rf_signal1.json)

**Predators stealing food:**
- Lower `distance_jump_m` (more sensitive)
- Reduce `retract_delay_ms` (retract faster)
- Ensure radar is enabled and connected
