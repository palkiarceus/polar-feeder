"""
Main Entry Point for Polar Feeder Controller

This module is the primary interface for the Polar Feeder system. It supports two main modes:

1. BLE_TEST Mode (--ble-test):
   - Starts a BLE (Bluetooth Low Energy) GATT server for remote control
   - Listens for commands like ENABLE=1, THREAT=1, etc.
   - Controls the feeder FSM via BLE commands
   - Logs all events and telemetry to CSV
   
2. DEMO Mode (default):
   - Runs a simulated feeder with random stillness data
   - Demonstrates CSV logging functionality
   - No actual hardware interaction needed

Configuration:
- Loads settings from config.json (feeder behavior, logging, radar, safety)
- Session logging with unique session IDs
- BLE interface for real-time control and monitoring

Usage:
    python main.py --config config/config.example.json --ble-test
    python main.py --demo-seconds 30.0
"""

import argparse
import random
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path
import threading
import subprocess
import signal
import os 
import sys  # Add this line

from polar_feeder.config.loader import load_config
from polar_feeder.logging.csv_logger import CsvSessionLogger, pick_log_dir
from polar_feeder.ble_interface import BleServer
from polar_feeder.radar import RadarReader
from polar_feeder.vision import VisionTracker, SensorFusion


def make_session_id() -> str:
    """
    Generate a unique session identifier.
    
    Format: {ISO8601_TIMESTAMP}_{8_CHAR_UUID}
    Example: 20260326T143022Z_a1b2c3d4
    
    Returns:
        String unique session ID combining timestamp and random UUID
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")  # ISO 8601 timestamp
    short = uuid.uuid4().hex[:8]                        # First 8 chars of UUID
    return f"{ts}_{short}"


def main() -> int:
    """
    Main entry point for the Polar Feeder controller.
    
    Parses command-line arguments, loads configuration, and runs either:
    - BLE test mode: Remote control via Bluetooth with FSM
    - Demo mode: Simulated logging without hardware
    
    Returns:
        Integer exit code (0 for success, non-zero for errors)
    """
    # ===== ARGUMENT PARSING =====
    parser = argparse.ArgumentParser(description="Polar feeder controller (CDR logging/config proof).")
    parser.add_argument("--config", default="config/config.example.json", help="Path to JSON config.")
    parser.add_argument("--test-id", default="", help="Optional test_id to include in logs.")
    parser.add_argument("--demo-seconds", type=float, default=10.0, help="How long to run demo loop.")
    parser.add_argument("--ble-test", action="store_true", help="Start BLE GATT server and handle commands.")
    args = parser.parse_args()

    # ===== LOAD CONFIGURATION =====
    cfg = load_config(args.config)
    
    # Debug: Print module locations
    import polar_feeder
    import polar_feeder.actuator as actmod
    print("[PATH] polar_feeder pkg:", polar_feeder.__file__, flush=True)
    print("[PATH] actuator mod:", actmod.__file__, flush=True)
    
    # ===== BLE TEST MODE =====
    # Remote control via Bluetooth with full FSM and logging
    camera_thread = None
    vision_active = False
    if args.ble_test:
        # Import actuator/FSM here to avoid dependencies in demo mode
        from polar_feeder.actuator import Actuator
        from polar_feeder.feeder_fsm import FeederFSM

        # ===== SESSION SETUP =====
        # Create unique session identifier and log file
        session_id = make_session_id()
        log_dir = pick_log_dir(cfg.logging.log_dir)
        log_path = log_dir / f"session_{session_id}.csv"
        logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
        logger.open()

        # ===== RUNTIME STATE =====
        # Dictionary to hold runtime parameters that can be changed via BLE commands
        # These are NOT persisted to config - they're for this session only
        runtime = {
            # Core control: enable/disable the feeder
            "enable": 0,
            
            # Behavior thresholds from config
            "stillness_threshold": float(cfg.stillness.trigger_threshold),
            "stillness_min_duration_s": float(cfg.stillness.min_duration_s),
            "stillness_publish_hz": float(cfg.stillness.publish_hz),
            
            # Logging configuration
            "log_enabled": bool(cfg.logging.enabled),
            "telemetry_hz": float(cfg.logging.telemetry_hz),
            "max_storage_mb": float(cfg.logging.max_storage_mb),
            
            # Radar and safety
            "radar_enabled": bool(cfg.radar.enabled),
            "ble_disconnect_safe_idle": bool(cfg.safety.ble_disconnect_safe_idle),
            "detection_distance_m": float(cfg.radar.detection_distance_m),  # Distance threshold to start game

            # Vision parameters (stillness tolerance)
            "vision_enabled": bool(cfg.vision.enabled),
            "motion_threshold": float(cfg.vision.motion_threshold),  # Stillness: movement allowed before threat
            "sync_window_s": float(cfg.vision.sync_window_s),

            # Actuator timing parameters
            "retract_delay_ms": int(cfg.actuator.retract_delay_ms),  # Delay: time from threat to retraction
            "pulse_ms": int(cfg.actuator.pulse_ms),                   # RF signal pulse duration
            "feeding_distance_m": float(cfg.actuator.feeding_distance_m),  # Distance for FEEDING state

            # Status tracking for monitoring
            "actuator_cmd": "IDLE",
            "feeder_state": "IDLE",
            "radar_last_bin": "",
            "radar_threat": 0,
            "enable_armed_at": 0.0,
            "vision_motion": 0.0,
            "fused_threat": 0,
        }

        # ===== INITIALIZE ACTUATOR AND FSM =====
        # Create the actuator (RF transmitter) and the state machine that controls it
        # Cooldown is not in config yet; choose something safe for testing
        COOLDOWN_S = 2.0
        act = Actuator()
        act.open()

        fsm = FeederFSM(
            actuator=act,
            retract_delay_ms=runtime["retract_delay_ms"],
            cooldown_s=COOLDOWN_S,
            motion_threshold=cfg.vision.motion_threshold,
            feeding_distance_m=cfg.actuator.feeding_distance_m,
            detection_distance_m=cfg.radar.detection_distance_m,
        )

        # ===== INITIALIZE BLE SERVER =====
        # Create BLE GATT server for remote control and monitoring
        ble = BleServer(name="PolarFeeder")
        
        # ===== INITIALIZE RADAR (OPTIONAL) =====
        # Start the radar sensor if enabled in config
        radar = None
        if cfg.radar.enabled:
            radar = RadarReader(
                port=cfg.radar.port,
                baud=cfg.radar.baud,
                timeout_s=cfg.radar.timeout_s,
                distance_jump_m=cfg.radar.distance_jump_m,
            )
            radar.start()
            print(f"[RADAR] started on {cfg.radar.port}", flush=True)
            
        # ===== INITIALIZE VISION (OPTIONAL) =====
        # Set up vision tracking and sensor fusion if enabled in config
        vision_tracker = None
        sensor_fusion = None
        if cfg.vision.enabled:
            vision_tracker = VisionTracker()
            sensor_fusion = SensorFusion(
                motion_threshold=cfg.vision.motion_threshold
            )
            print(f"[VISION] initialized with motion_threshold={cfg.vision.motion_threshold}", flush=True)
        # At the top of the BLE test section, add these variables (around line 175-180)
        camera = None
        camera_thread = None
        vision_active = False
        # Add these variables near the top of the BLE test section (around line 175)
        camera_running = False
        fsm_autonomous = False
        camera_process = None
        def process_camera_frames(picam2):
            """Background thread to process camera frames and detect threats."""
            global vision_active
            while vision_active:
                try:
                    # Capture frame
                    frame = picam2.capture_array()
            
                    # Run YOLO inference here (you'll integrate your detection)
                    # For now, just log that we're capturing
                    print("[CAMERA] Frame captured", flush=True)
            
                    # Control frame rate (e.g., 5 fps to reduce CPU)
                    time.sleep(0.2)
                except Exception as e:
                    print(f"[CAMERA] Error: {e}", flush=True)
                    break
                    
        def camera_loop():
            """Thread to capture frames from PiCamera2 and update FSM."""
            global vision_active
            from picamera2 import Picamera2
            import cv2
            from polar_feeder.yolo_detect import detect_frame  # Implement this in yolo_detect.py

            try:
                picam2 = Picamera2()
                config = picam2.create_preview_configuration(main={"size": (640,480),"format":"RGB888"})
                picam2.configure(config)
                picam2.start()
                print("[CAMERA] Thread started", flush=True)

                while vision_active:
                    frame = picam2.capture_array()

                    # Run YOLO detection: returns threat=True/False, motion magnitude float
                    threat, motion = detect_frame(frame)

                    # Update FSM tick with motion/threat info
                    fsm.tick(
                        enable=True,
                        threat=threat,
                        motion_magnitude=motion,
                        radar_distance_m=None,
                        now=time.monotonic()
                    )

                    # Update runtime vision motion for STATUS queries
                    runtime["vision_motion"] = motion

                    # Limit frame rate
                    time.sleep(0.2)  # ~5 FPS

            except Exception as e:
                print(f"[CAMERA] Error in camera_loop: {e}", flush=True)
            finally:
                print("[CAMERA] Thread stopped", flush=True)
                            
        def start_camera():
            """Initialize and start the PiCamera for vision tracking."""
            from picamera2 import Picamera2
            import cv2
    
            picam2 = Picamera2()
            # Configure for preview size (e.g., 640x480 for performance)
            config = picam2.create_preview_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
            picam2.configure(config)
            picam2.start()
            return picam2
        
        
        # Define the callback function for handling incoming BLE commands
        # This function parses commands and updates the runtime state
        # Define the callback function for handling incoming BLE commands
        def handle_ble(cmd) -> str:
            """
            BLE command handler - processes incoming commands from BLE clients.
            """
            import sys  # Add this import here
            
            # Get the current Python executable (from the virtual environment)
            CURRENT_PYTHON = sys.executable
            print(f"[DEBUG] Using Python: {CURRENT_PYTHON}", flush=True)
            print(f"[DEBUG] handle_ble raw={cmd.raw!r}", flush=True)
            
            s = cmd.raw.strip()
            if not s:
                return "ERR EMPTY"
            s_up = s.upper()

            # ===== ENABLE=0/1: Control feeder enable status =====
            if s_up.startswith("ENABLE="):
<<<<<<< HEAD
                val = s.split("=", 1)[1].strip()
                print("[DEBUG] parsed val =", val, flush=True)
                if val not in ("0", "1"):
                    return "ERR BAD_VALUE ENABLE"


=======
                val = s.split("=",1)[1].strip()
>>>>>>> 32b668566db4333924aaec0d4ef2884882a4dae2
                prev = runtime["enable"]
                runtime["enable"] = int(val)

                print(f"[DEBUG] ENABLE={val}, cfg.vision.enabled={cfg.vision.enabled}", flush=True)

                if runtime["enable"] == 1:
                    runtime["enable_armed_at"] = time.monotonic()
                    if radar:
                        radar.reset_baseline()

                    # Start camera thread using Picamera2
                    global camera_thread, vision_active
                    if cfg.vision.enabled and not vision_active:
                        vision_active = True
                        camera_thread = threading.Thread(target=camera_loop, daemon=True)
                        camera_thread.start()
                        print("[ENABLE] Camera thread started (Picamera2)", flush=True)

                    # Log event
                    logger.log_event(
                        state="BLE_TEST",
                        enable_flag=runtime["enable"],
                        command="ENABLE",
                        result=f"{prev}->{runtime['enable']}",
                        notes="System enabled - camera and FSM active",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )

                else:  # runtime["enable"] == 0
                    if vision_active:
                        vision_active = False
                        if camera_thread:
                            camera_thread.join(timeout=1.0)
                            camera_thread = None
                        print("[ENABLE] Camera thread stopped", flush=True)

                    # Log event
                    logger.log_event(
                        state="BLE_TEST",
                        enable_flag=runtime["enable"],
                        command="ENABLE",
                        result=f"{prev}->{runtime['enable']}",
                        notes="System disabled - camera and FSM stopped",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )

                return f"ACK ENABLE={runtime['enable']}"

            # ===== ACTUATOR=EXTEND/RETRACT: Manual arm control =====
            if s_up.startswith("ACTUATOR="):
                val = s.split("=", 1)[1].strip().upper()
                print("[DEBUG] ACTUATOR cmd ->", val, flush=True)

                if val not in ("EXTEND", "RETRACT"):
                    return "ERR BAD_VALUE ACTUATOR"

                runtime["actuator_cmd"] = val

                try:
                    if val == "EXTEND":
                        act.extend()
                    else:
                        act.retract()
                except Exception as e:
                    import traceback
                    print("[DEBUG] actuator exception:", traceback.format_exc(), flush=True)
                    return f"ERR ACTUATOR_FAIL {type(e).__name__}"
                    
                return f"ACK ACTUATOR={val}"
            
            # ===== RETRACT: Manual retraction from FEEDING state =====
            if s_up == "RETRACT":
                success = fsm.manual_retract()
                if success:
                    logger.log_event(
                        state="FEEDING",
                        enable_flag=runtime["enable"],
                        command="RETRACT",
                        result="SUCCESS",
                        notes="Manual retraction from FEEDING state",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )
                    return "ACK RETRACT"
                else:
                    return "ERR RETRACT not_in_feeding_state"
            
            # ===== VISION=<detection_line>: Process YOLO detection =====
            if s_up.startswith("VISION="):
                if not cfg.vision.enabled:
                    return "ERR VISION_DISABLED"
                
                detection_line = s.split("=", 1)[1].strip()
                try:
                    det = vision_tracker.parse_line(detection_line)
                    if det is None:
                        return "ERR VISION_PARSE_FAILED"
                    
                    motion = vision_tracker.compute_motion(det)
                    sensor_fusion.update_vision(det.timestamp)
                    
                    runtime["vision_motion"] = motion
                    
                    return f"ACK VISION motion={motion:.2f}"
                except Exception as e:
                    return f"ERR VISION_EXCEPTION {type(e).__name__}"
            
            # SET key=value for runtime parameters
            if s_up.startswith("SET "):
                rest = s[4:].strip()
                if "=" not in rest:
                    return "ERR BAD_FORMAT SET"
                key, value = [x.strip() for x in rest.split("=", 1)]
                key_l = key.lower()

                if key_l not in runtime or key_l == "enable":
                    return f"ERR UNKNOWN_KEY {key}"

                def to_bool01(v: str) -> bool:
                    if v not in ("0", "1"):
                        raise ValueError
                    return (v == "1")

                try:
                    if key_l == "stillness_threshold":
                        f = float(value)
                        if f > 1.0 and f <= 100.0:
                            f = f / 100.0
                        if not (0.0 <= f <= 1.0):
                            return "ERR OUT_OF_RANGE stillness_threshold 0 1"
                        runtime[key_l] = f

                    elif key_l == "stillness_min_duration_s":
                        f = float(value)
                        if not (0.0 <= f <= 60.0):
                            return "ERR OUT_OF_RANGE stillness_min_duration_s 0 60"
                        runtime[key_l] = f

                    elif key_l == "retract_delay_ms":
                        n = int(value)
                        if not (0 <= n <= 3000):
                            return "ERR OUT_OF_RANGE retract_delay_ms 0 3000"
                        runtime[key_l] = n
                        fsm.retract_delay_s = n / 1000.0

                    elif key_l == "pulse_ms":
                        n = int(value)
                        if not (50 <= n <= 1000):
                            return "ERR OUT_OF_RANGE pulse_ms 50 1000"
                        runtime[key_l] = n
                        try:
                            act.pulse_s = n / 1000.0
                        except Exception:
                            pass

                    elif key_l == "stillness_publish_hz":
                        f = float(value)
                        if not (1.0 <= f <= 30.0):
                            return "ERR OUT_OF_RANGE stillness_publish_hz 1 30"
                        runtime[key_l] = f

                    elif key_l == "telemetry_hz":
                        f = float(value)
                        if not (1.0 <= f <= 30.0):
                            return "ERR OUT_OF_RANGE telemetry_hz 1 30"
                        runtime[key_l] = f

                    elif key_l == "max_storage_mb":
                        f = float(value)
                        if not (10.0 <= f <= 5000.0):
                            return "ERR OUT_OF_RANGE max_storage_mb 10 5000"
                        runtime[key_l] = f

                    elif key_l in ("radar_enabled", "log_enabled", "ble_disconnect_safe_idle"):
                        runtime[key_l] = to_bool01(value)

                    else:
                        runtime[key_l] = value

                except ValueError:
                    return f"ERR TYPE {key} bad_value"

                logger.log_event(
                    state="BLE_TEST",
                    enable_flag=runtime["enable"],
                    command=f"SET {key_l}",
                    result=str(runtime[key_l]),
                    notes="Config parameter set (not persisted yet)",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK {key_l}={runtime[key_l]}"

            if s_up.startswith("GET "):
                key = s[4:].strip()
                key_l = key.lower()
                if key_l not in runtime:
                    return f"ERR UNKNOWN_KEY {key}"
                return f"ACK {key_l}={runtime[key_l]}"

            if s_up in ("GET STATUS", "STATUS"):
                vision_status = ""
                if cfg.vision.enabled:
                    vision_status = f" vision_enabled=1 vision_motion={runtime['vision_motion']:.2f} fused_threat={runtime['fused_threat']}"
                
                return (
                    "ACK STATUS "
                    f"enable={runtime['enable']} "
                    f"feeder_state={runtime['feeder_state']} "
                    f"retract_delay_ms={runtime['retract_delay_ms']} "
                    f"pulse_ms={runtime['pulse_ms']} "
                    f"radar_enabled={int(runtime['radar_enabled'])}"
                    f"{vision_status}"
                )

            return "ERR UNKNOWN_CMD"

        ble.set_command_handler(handle_ble)

        t = threading.Thread(target=ble.start, daemon=True)
        t.start()

        logger.log_event(
            state="BLE_TEST",
            enable_flag=runtime["enable"],
            command="SESSION",
            result="START",
            notes=f"BLE test started; config={Path(args.config).as_posix()}",
            radar_enabled=runtime["radar_enabled"],
            radar_zone="",
        )

        BLE_TIMEOUT_S = 30.0
        BLE_TIMEOUT_S = 30.0
        print("BLE test mode running. Send newline-terminated commands (end with \\n).", flush=True)
        print(f"Session log: {log_path}", flush=True)

        try:
            # Run FSM at a steady tick rate
            tick_hz = 20.0
            tick_dt = 1.0 / tick_hz
            last_radar_seq = -1
            last_radar_seq = -1

            # ===== MAIN CONTROL LOOP =====
            # Run continuously, updating FSM state based on enable signal and threat detection
            while True:
                # ===== SAFETY: BLE INACTIVITY TIMEOUT =====
                # If BLE disconnect causes safety mode, force feeder to disable
                # This prevents the feeder from running uncontrolled if the controller disconnects
                if runtime["ble_disconnect_safe_idle"] and runtime["enable"] == 1:
                    if (time.time() - ble.last_rx_time) > BLE_TIMEOUT_S:
                        runtime["enable"] = 0  # Force disable
                        logger.log_event(
                            state="BLE_TEST",
                            enable_flag=runtime["enable"],
                            command="SAFETY",
                            result="BLE_TIMEOUT->ENABLE=0",
                            notes="Forced safe idle due to BLE inactivity timeout",
                            radar_enabled=runtime["radar_enabled"],
                            radar_zone="",
                        )
                        ble.notify("ACK ENABLE=0\n")

                # ===== THREAT DETECTION =====
                # Check radar for threats (approaching animals)
                threat = False
                radar_zone = ""
                radar_notes = ""

                # Radar arming delay: Don't allow threat detection immediately after enable
                # This gives the animal time to approach (prevents false triggers)
                RADAR_ARM_DELAY_S = 1.5
                fsm_state_name = getattr(fsm.state, "name", str(fsm.state))

                # Radar is only active when:
                # 1. Feeder is enabled
                # 2. Radar is enabled in config
                # 3. Arming delay has elapsed
                # 4. FSM is in LURE state (arm is extended)
                radar_allowed = (
                    runtime["enable"] == 1
                    and runtime["radar_enabled"]
                    and (time.monotonic() - runtime["enable_armed_at"]) >= RADAR_ARM_DELAY_S
                    and fsm_state_name == "LURE"
                )

                if radar and radar_allowed:
                    rr = radar.get_latest()
                    if rr.valid and rr.seq != last_radar_seq:
                        last_radar_seq = rr.seq
                    if rr.valid and rr.seq != last_radar_seq:
                        last_radar_seq = rr.seq
                        radar_zone = str(rr.bin_index) if rr.bin_index is not None else ""
                        threat = rr.threat
                        radar_notes = rr.raw_line
                        print(
                            f"[RADAR] NEW seq={rr.seq} bin={rr.bin_index} dist={rr.distance_m} threat={rr.threat}",
                            f"[RADAR] NEW seq={rr.seq} bin={rr.bin_index} dist={rr.distance_m} threat={rr.threat}",
                            flush=True,
                        )
                        
                        # Update sensor fusion with radar timestamp
                        if sensor_fusion:
                            sensor_fusion.update_radar(rr.timestamp)

                # ===== SENSOR FUSION =====
                # Combine radar and vision threats
                fused_threat = threat
                motion_magnitude = runtime.get("vision_motion", 0.0)
                radar_distance_m = rr.distance_m if radar and radar_allowed and 'rr' in locals() and rr.valid else None
                
                if sensor_fusion:
                    # Check if sensors are in sync
                    in_sync = sensor_fusion.in_sync(runtime["sync_window_s"])
                    if not in_sync:
                        pass #
                        # print(f"[FUSION] sensors out of sync", flush=True)
                    
                    # Get fused threat decision
                    fused_threat = sensor_fusion.fused_threat(threat, motion_magnitude)
                    runtime["fused_threat"] = int(fused_threat)
                    
                    if fused_threat and not threat:
                        print(f"[FUSION] vision motion triggered threat (motion={motion_magnitude:.2f})", flush=True)

                # Tick the state machine with fused threat, motion data, and radar distance
                now = time.monotonic()
                fsm.tick(
                    enable=bool(runtime["enable"]), 
                    threat=fused_threat,
                    motion_magnitude=motion_magnitude if cfg.vision.enabled else None,
                    radar_distance_m=radar_distance_m,
                    now=now
                )

                # Report state for STATUS queries
                new_state = getattr(fsm.state, "name", str(fsm.state))
                if new_state != runtime["feeder_state"]:
                    print(f"[FSM] {runtime['feeder_state']} -> {new_state}", flush=True)
                runtime["feeder_state"] = new_state
                new_state = getattr(fsm.state, "name", str(fsm.state))
                if new_state != runtime["feeder_state"]:
                    print(f"[FSM] {runtime['feeder_state']} -> {new_state}", flush=True)
                runtime["feeder_state"] = new_state
                
                runtime["radar_last_bin"] = radar_zone
                runtime["radar_threat"] = int(threat)
                
                time.sleep(tick_dt)

        except KeyboardInterrupt:
            # Clean shutdown on Ctrl+C
            logger.log_event(
                state="BLE_TEST",
                enable_flag=runtime["enable"],
                command="SESSION",
                result="STOP",
                notes="BLE test stopped by user",
                radar_enabled=runtime["radar_enabled"],
                radar_zone="",
            )
            try:
                if radar:
                    radar.stop()
            except Exception:
                pass
                
            try:
                act.close()
            except Exception:
                pass
            logger.close()
            print("BLE test stopped.", flush=True)
            return 0

    # ===== DEMO LOGGING MODE =====
    # Alternative mode (default): Simulated feeder with random stillness data
    # Used for testing logging functionality without requiring hardware
    # Session concept: enable_flag transitions 0->1 starts logging
    enable_flag = 1
    state = "ENABLED"

    session_id = make_session_id()

    log_dir = pick_log_dir(cfg.logging.log_dir)
    log_path = log_dir / f"session_{session_id}.csv"

    logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
    logger.open()

    # Event: session start
    logger.log_event(
        state=state,
        enable_flag=enable_flag,
        command="enable_flag",
        result="0->1",
        notes=f"Session start; config={Path(args.config).as_posix()}",
        radar_enabled=cfg.radar.enabled,
        radar_zone="",
    )

    # Calculate telemetry interval based on configured frequency
    telemetry_hz = float(cfg.logging.telemetry_hz)
    period_s = 1.0 / telemetry_hz

    # Simulated stillness sensor data
    # raw: Random value between 0 (movement) and 1 (stillness)
    # filtered: Low-pass filtered version to smooth out noise
    still_f = 0.0

    # Run for specified duration
    t_end = time.time() + float(args.demo_seconds)
    while time.time() < t_end:
        # Generate simulated raw stillness reading (random 0-1)
        still_raw = random.random()
        
        # Apply low-pass filter (exponential moving average)
        # Alpha=0.2: gives recent samples 20% weight, historical 80% weight
        alpha = 0.2
        still_f = (1 - alpha) * still_f + alpha * still_raw

        # Log telemetry data point
        logger.log_telemetry(
            state=state,
            enable_flag=enable_flag,
            stillness_raw=still_raw,
            stillness_filtered=still_f,
            radar_enabled=cfg.radar.enabled,
            radar_zone="DISABLED" if not cfg.radar.enabled else "UNKNOWN",
        )
        time.sleep(period_s)

    # Event: session end (disable)
    enable_flag = 0
    state = "IDLE"
    logger.log_event(
        state=state,
        enable_flag=enable_flag,
        command="enable_flag",
        result="1->0",
        notes="Session end (demo complete)",
        radar_enabled=cfg.radar.enabled,
        radar_zone="",
    )

    logger.close()

    print(f"Wrote session log: {log_path}")
    return 0


if __name__ == "__main__":
    # Entry point: Run main() and exit with its return code
    raise SystemExit(main())
