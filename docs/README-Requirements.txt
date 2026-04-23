# Polar Feeder

Automatic polar bear feeder using YOLOv8 computer vision on a Raspberry Pi 5.
Detects a polar bear via camera, trains still-hunting behavior, and controls
a linear actuator food tray via RF signals. Managed remotely over BLE from an Android app.

---

## Hardware Requirements

- Raspberry Pi 5
- IMX708 camera module (Pi Camera Module 3 or compatible)
- RF radar board connected via UART
- RF transmitter on GPIO17 (controls actuator via pre-recorded signal files)
- Android device for BLE control

---

## Setup

### 1. System dependencies (apt)

These packages are OS-managed and cannot be installed via pip.
Run on a fresh Raspberry Pi OS install before anything else:

```bash
sudo apt update && sudo apt install -y \
    python3-picamera2 \
    python3-libcamera \
    python3-lgpio \
    python3-gpiod \
    bluetooth \
    bluez \
    libbluetooth-dev \
    libatlas-base-dev \
    libjpeg-dev \
    python3-dev
```

### 2. Virtual environment

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
```

> **The `--system-site-packages` flag is required.** Without it, the venv
> cannot see `picamera2` and `lgpio`, which are apt-installed and cannot be
> reinstalled via pip on the Pi.

### 3. pip dependencies

```bash
pip install -r requirements.txt
```

> `torch` and `torchvision` are pulled in automatically as `ultralytics`
> dependencies. First install may take several minutes on the Pi.

### 4. RF signal files

The actuator uses pre-recorded RF pulse files. Record your remote's signals:

```bash
# Record the EXTEND signal (button 1 on your remote)
python receivingsave.py
# Saves to rf_signal1.json — rename/move as needed

# Record the RETRACT signal (button 2 on your remote)
python receivingsave.py
# Saves to rf_signal2.json — rename/move as needed
```

Signal files are searched in: `config/rf/` (preferred) or `src/pi/polar_feeder/config/` (legacy).

### 5. Configuration

```bash
cp src/pi/polar_feeder/config/config.example.json \
   src/pi/polar_feeder/config/config.json
```

Key fields to review:

| Field | Default | Description |
|---|---|---|
| `vision.motion_threshold` | 35 | Pixel displacement to trigger threat |
| `vision.enabled` | true | Enable/disable vision-based threat detection |
| `actuator.retract_delay_ms` | 500 | Delay between threat detected and retraction |
| `actuator.feeding_distance_m` | 0.5 | Distance (m) at which bear enters FEEDING state |
| `radar.enabled` | false | Enable radar board (requires serial connection) |
| `radar.port` | `/dev/ttyAMA0` | Serial port for radar board |
| `radar.detection_distance_m` | 3.0 | Distance at which game logic activates |

---

## Running

### Vision pipeline (camera + YOLO + FSM)

```bash
cd src/pi
source ../../.venv/bin/activate

python -m polar_feeder.yolo_detect \
    --model yolov8n.pt \
    --source picamera0 \
    --skip 2 \
    --thresh 0.3
```

**Arguments:**

| Argument | Default | Description |
|---|---|---|
| `--model` | required | Path to YOLOv8 `.pt` model file |
| `--source` | required | `picamera0`, `usb0`, video file, or image/folder path |
| `--skip` | `1` | Run inference every N frames (`2` recommended for Pi 5) |
| `--thresh` | `0.5` | Confidence threshold — `0.3` recommended for distant detection |
| `--resolution` | camera default | Override resolution e.g. `640x480` |
| `--record` | off | Save output video to `demo1.avi` (requires `--resolution`) |

**On-screen keyboard controls** (OpenCV window must be focused):

| Key | Action |
|---|---|
| `q` | Quit |
| `s` | Pause |
| `p` | Save screenshot as `capture.png` |

### Full system with BLE control

```bash
python -m polar_feeder.main \
    --config src/pi/polar_feeder/config/config.example.json \
    --ble-test
```

Connect with the Android app and send commands:

| Command | Description |
|---|---|
| `ENABLE=1` / `ENABLE=0` | Enable or disable the feeder |
| `ACTUATOR=EXTEND` | Manually extend the arm |
| `ACTUATOR=RETRACT` | Manually retract the arm |
| `SET retract_delay_ms=500` | Change retract delay live |
| `SET motion_threshold=35` | Change motion sensitivity live |
| `GET STATUS` | Query current system state |
| `RETRACT` | Manual retraction from FEEDING state |

### Environment self-test (no hardware required)

Run this on a fresh Pi to verify the environment before full operation:

```bash
python selftest.py
```

Checks Python path, log write access, UART devices, Bluetooth adapter, camera enumeration, and GPIO library imports.

### Software unit tests (no hardware required)

```bash
python test_software.py
```

Tests config loading, CSV logging, FSM logic, actuator interface (mocked), and vision/fusion — all without any physical hardware.

---

## FSM State Reference

```
IDLE ──(enabled)──► LURE ──(threat or motion ≥ threshold)──► RETRACT_WAIT
 ▲                    │                                            │
 │                    │ (bear within feeding_distance_m)           │ (after retract_delay_ms)
 │                    ▼                                            ▼
 │                 FEEDING ◄──(manual_retract only)          COOLDOWN
 │                                                                 │
 └─────────────────────────────────────────────────────────────────┘
                                                    (after cooldown_s)
```

**State descriptions:**

| State | Arm | Description |
|---|---|---|
| `IDLE` | Retracted | Waiting to be enabled |
| `LURE` | Extended | Food out, watching for bear |
| `FEEDING` | Extended | Bear at feeding distance — stays extended until manual retract |
| `RETRACT_WAIT` | Extended | Threat detected, waiting `retract_delay_ms` before pulling back |
| `COOLDOWN` | Retracted | Cooling down before next extend cycle |

---

## Project Structure

```
polar-feeder/
├── config/
│   └── rf/                    # RF signal JSON files (rf_signal1.json, rf_signal2.json)
├── src/pi/polar_feeder/
│   ├── config/
│   │   ├── config.example.json
│   │   └── loader.py          # Config dataclasses + validation
│   ├── logging/
│   │   └── csv_logger.py      # Session telemetry/event CSV logger
│   ├── actuator.py            # Actuator interface (extend/retract)
│   ├── ble_interface.py       # BLE GATT server (Nordic UART Service)
│   ├── feeder_fsm.py          # Finite state machine
│   ├── main.py                # BLE mode + demo mode entry point
│   ├── radar.py               # Serial radar reader (threaded)
│   ├── transmittingfunc.py    # Low-level RF pulse transmission via lgpio
│   ├── vision.py              # VisionTracker + SensorFusion
│   └── yolo_detect.py         # Camera + YOLO + FSM pipeline
├── receivingsave.py           # RF signal recording utility
├── selftest.py                # Environment validation script
├── test_software.py           # Software unit tests (no hardware)
├── requirements.txt
└── README.md
```

---

## Notes

- **`--system-site-packages` is not optional** — `picamera2` depends on system libcamera
  libraries that pip cannot install. The venv must bridge to system packages.
- **`torch` on Pi 5 is CPU-only** and takes ~400ms per inference at 640×480.
  Use `--skip 2` or higher to keep the display fluid while inference runs in the background.
- **`lgpio` may need to be run as root or with GPIO group membership.**
  Add your user: `sudo usermod -aG gpio $USER` then log out and back in.
- **BLE requires the bluetooth service running:** `sudo systemctl enable bluetooth && sudo systemctl start bluetooth`
- The motion threshold (default 35) was tuned for a bear image at close range on a 640×480 feed.
  Adjust `vision.motion_threshold` in config if you get too many false triggers or missed detections.