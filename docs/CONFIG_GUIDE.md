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
  "distance_jump_m": 0.20,      // Sudden distance change threshold for threat
  "detection_distance_m": 3.0   // [DISTANCE] Game starts when bear enters this distance
}
```

**What it does:**
- Monitors for approaching animals
- Detects sudden distance changes (animal rushing in)
- Triggers threat signals only when bear is within `detection_distance_m`
- Prevents predators from stealing food

**Port Configuration:**
- Raspberry Pi: `/dev/ttyAMA0` (GPIO 14/15)
- USB adapter: `/dev/ttyUSB0` or similar
- Windows: `COM3`, `COM4`, etc.

**Typical values:**
- `distance_jump_m`: 0.10-0.50m (0.20 is moderate sensitivity)
- `detection_distance_m`: 2.0-5.0m (**user configurable**) - distance at which the "game" starts
  - If bear is beyond this distance, radar readings are ignored
  - Once bear enters within this distance, threat detection is active
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
  "retract_delay_ms": 2500,      // [DELAY] Time from threat detection to retraction (milliseconds)
  "pulse_ms": 200,               // RF signal pulse duration
  "feeding_distance_m": 0.5      // Distance for FEEDING state (when bear "wins")
}
```

**What it does:**
- Controls how long arm stays extended for food dispensing
- Controls RF signal transmission parameters
- Defines safe feeding proximity

**retract_delay_ms (CONFIGURABLE - DELAY parameter):**
- Range: 0-3000ms (0-3 seconds)
- **User-configurable via BLE: `SET retract_delay_ms=<value>`**
- 500-1000ms: Small food portions, quick retraction
- 1500-2500ms: Medium portions, moderate retraction delay
- 2500-3000ms: Large portions, longer delay for eating
- **This is the delay between threat detection and actual arm retraction**

**pulse_ms:**
- Range: 50-1000ms
- Controls RF signal shape (usually 100-200ms)
- Shouldn't need to change unless RF hardware changes

**feeding_distance_m:**
- Range: 0.1-5.0 meters
- Distance at which bear is considered "close enough to feed"
- When bear reaches this distance, FSM enters FEEDING state
- Allows bear to safely eat without retraction
- Typical: 0.3-1.0 meters depending on feeder size

### 6. Vision (Computer Vision Threat Detection)

```json
"vision": {
  "enabled": true,            // Enable/disable computer vision processing
  "motion_threshold": 20.0,   // [STILLNESS] Max pixel movement allowed before threat
  "sync_window_s": 0.5        // Max time difference between radar/vision for fusion
}
```

**What it does:**
- Processes YOLO object detection data from camera
- Tracks bounding box movement over time
- Combines with radar data for robust threat detection
- Triggers threat if bear moves more than `motion_threshold`

**motion_threshold (CONFIGURABLE - STILLNESS parameter):**
- Range: 0.0-1000.0 pixels
- **User-configurable via BLE: `SET motion_threshold=<value>`**
- Lower = more sensitive (small movements trigger threat)
  - 5.0-15.0: Very sensitive (bear can barely move)
  - 20.0-40.0: Moderate (bear can shift position slightly)
  - 50.0+: Tolerant (bear can make several movements)
- Higher = more tolerant (bear can move more freely without triggering)
- **This is the "Stillness" tolerance - how much movement is allowed before threat**
- Higher = less sensitive (only large movements trigger)
- Typical: 10.0-50.0 for moderate sensitivity

**sync_window_s:**
- Range: 0.1-5.0 seconds
- Time window for radar/vision timestamp alignment
- Smaller = stricter synchronization required
- Typical: 0.3-1.0 seconds

**Integration:**
- Vision data sent via BLE: `VISION=1,1690001123.45,50,200,30,180`
- Format: `id,timestamp,ymin,ymax,xmin,xmax`
- Movement calculated as Euclidean distance of bounding box center

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

## YOLO Vision Integration

### YOLO Output Format Support

The vision module (`polar_feeder.vision`) supports parsing YOLO object detection output in two formats:

**Format 1: YOLO output.txt (text file format)**
```
Detection number: 1
Time: 1690001123.45
Xmin = 30
Xmax = 180
Ymin = 50
Ymax = 200
```

**Format 2: CSV (faster, BLE-friendly)**
```
1,1690001123.45,50,200,30,180
```
(id, timestamp, ymin, ymax, xmin, xmax)

### Sending YOLO Data via BLE

Once the YOLO model detects a bear, send the detection via BLE:

```
VISION=1,1690001123.45,50,200,30,180
```

Response: `ACK VISION motion=5.67`

The system will:
1. Parse the detection (bounding box coordinates)
2. Compute motion magnitude (center movement from previous frame)
3. Compare to `motion_threshold` (Stillness parameter)
4. Trigger threat if movement > threshold
5. Fuse with radar data for robust threat detection

### Three Configuration Parameters Summary

| Parameter | Location | Purpose | Range | User-Config |
|-----------|----------|---------|-------|-------------|
| **Delay** | `actuator.retract_delay_ms` | Time from threat to retraction | 0-3000ms | ✅ BLE: `SET retract_delay_ms=<ms>` |
| **Distance** | `radar.detection_distance_m` | Game start distance | 0.5-50m | ✅ Config: Edit JSON |
| **Stillness** | `vision.motion_threshold` | Movement tolerance | 0-1000px | ✅ BLE: `SET motion_threshold=<px>` |

All three parameters can be adjusted without restarting the system for real-time tuning during testing.

