"""
Main Entry Point for Polar Feeder Controller

Supports two modes:

1. BLE_TEST Mode (--ble-test):
   - Starts a BLE GATT server for remote control via Android app
   - On ENABLE=1: starts camera thread running YOLO + FSM
   - On ENABLE=0: stops camera thread cleanly
   - Logs all events and telemetry to CSV

2. DEMO Mode (default):
   - Simulated feeder with random stillness data
   - Tests CSV logging without hardware
"""

import argparse
import random
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path
import threading

from polar_feeder.config.loader import load_config
from polar_feeder.logging.csv_logger import CsvSessionLogger, pick_log_dir
from polar_feeder.ble_interface import BleServer
from polar_feeder.radar import RadarReader
from polar_feeder.vision import VisionTracker, SensorFusion


def make_session_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def main() -> int:
    # ===== ARGUMENT PARSING =====
    parser = argparse.ArgumentParser(description="Polar feeder controller.")
    parser.add_argument("--config", default="config/config.example.json", help="Path to JSON config.")
    parser.add_argument("--test-id", default="", help="Optional test_id to include in logs.")
    parser.add_argument("--demo-seconds", type=float, default=10.0, help="How long to run demo loop.")
    parser.add_argument("--ble-test", action="store_true", help="Start BLE GATT server and handle commands.")
    args = parser.parse_args()

    # ===== LOAD CONFIGURATION =====
    cfg = load_config(args.config)

    import polar_feeder
    import polar_feeder.actuator as actmod
    print("[PATH] polar_feeder pkg:", polar_feeder.__file__, flush=True)
    print("[PATH] actuator mod:", actmod.__file__, flush=True)

    # ===== BLE TEST MODE =====
    if args.ble_test:
        from polar_feeder.actuator import Actuator
        from polar_feeder.feeder_fsm import FeederFSM

        # ===== SESSION SETUP =====
        session_id = make_session_id()
        log_dir = pick_log_dir(cfg.logging.log_dir)
        log_path = log_dir / f"session_{session_id}.csv"
        logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
        logger.open()

        # ===== RUNTIME STATE =====
        runtime = {
            "enable": 0,
            "stillness_threshold": float(cfg.stillness.trigger_threshold),
            "stillness_min_duration_s": float(cfg.stillness.min_duration_s),
            "stillness_publish_hz": float(cfg.stillness.publish_hz),
            "log_enabled": bool(cfg.logging.enabled),
            "telemetry_hz": float(cfg.logging.telemetry_hz),
            "max_storage_mb": float(cfg.logging.max_storage_mb),
            "radar_enabled": bool(cfg.radar.enabled),
            "ble_disconnect_safe_idle": bool(cfg.safety.ble_disconnect_safe_idle),
            "detection_distance_m": float(cfg.radar.detection_distance_m),
            "vision_enabled": bool(cfg.vision.enabled),
            "motion_threshold": float(cfg.vision.motion_threshold),
            "sync_window_s": float(cfg.vision.sync_window_s),
            "retract_delay_ms": int(cfg.actuator.retract_delay_ms),
            "pulse_ms": int(cfg.actuator.pulse_ms),
            "feeding_distance_m": float(cfg.actuator.feeding_distance_m),
            "actuator_cmd": "IDLE",
            "feeder_state": "IDLE",
            "radar_last_bin": "",
            "radar_threat": 0,
            "enable_armed_at": 0.0,
            "vision_motion": 0.0,
            "fused_threat": 0,
        }

        # ===== CAMERA THREAD STATE =====
        # Use a plain dict as a mutable closure container so the BLE callback
        # (which runs in a different thread) can reliably read and write these
        # values. Plain variables or 'global' declarations don't work correctly
        # when the variables are defined inside a function scope like this.
        cam_state = {
            "active": False,       # True while camera thread should be running
            "thread": None,        # threading.Thread object (or None)
        }

        # ===== INITIALIZE ACTUATOR AND FSM =====
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
        ble = BleServer(name="PolarFeeder")

        # ===== INITIALIZE RADAR (OPTIONAL) =====
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
        vision_tracker = None
        sensor_fusion = None
        if cfg.vision.enabled:
            vision_tracker = VisionTracker()
            sensor_fusion = SensorFusion(
                base_motion_threshold=cfg.vision.motion_threshold,
                detection_distance_m=cfg.radar.detection_distance_m,
                feeding_distance_m=cfg.actuator.feeding_distance_m,
            )
            print(f"[VISION] initialized with adaptive motion_threshold (base={cfg.vision.motion_threshold})", flush=True)

        # ===== CAMERA THREAD FUNCTION =====
        def camera_loop():
            """
            Background thread: captures frames from PiCamera2, runs YOLO inference,
            computes motion, and ticks the FSM. Runs until cam_state['active'] is False.

            This function intentionally avoids any global declarations. It accesses
            shared state through the 'cam_state', 'runtime', and 'fsm' closure variables
            which are safe to read/write from multiple threads because:
            - cam_state['active'] is a simple bool read/write (GIL-protected)
            - runtime dict writes are also GIL-protected for single key updates
            - fsm.tick() is called only from this thread while enabled
            """
            from picamera2 import Picamera2
            import cv2
            from ultralytics import YOLO

            print("[CAMERA] Thread starting...", flush=True)

            try:
                # Load YOLO model
                model = YOLO("yolov8n.pt", task="detect")
                local_tracker = VisionTracker()

                # Initialize camera
                picam2 = Picamera2()
                cam_cfg = picam2.create_video_configuration(
                    main={"format": "XRGB8888", "size": (640, 480)}
                )
                picam2.configure(cam_cfg)
                picam2.start()
                print("[CAMERA] PiCamera2 started at 640x480", flush=True)

                frame_index = 0
                SKIP = 2  # Run inference every 2 frames

                while cam_state["active"]:
                    if not cam_state["active"]:
                        break
                    # Grab frame and convert BGRA -> BGR
                    frame_bgra = picam2.capture_array()
                    frame = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
                    frame_index += 1

                    # Only run inference every SKIP frames
                    if frame_index % SKIP != 0:
                        continue

                    # Run YOLO inference (bear = class 21)
                    results = model(frame, classes=[21], verbose=False)
                    detections = results[0].boxes

                    motion = 0.0
                    if len(detections) > 0:
                        det_box = detections[0]
                        xyxy = det_box.xyxy.cpu().numpy().squeeze()
                        xmin, ymin, xmax, ymax = xyxy.astype(int)

                        yolo_block = (
                            f"Detection number: {frame_index}\n"
                            f"Time: {time.perf_counter()}\n"
                            f"Xmin = {xmin}\n"
                            f"Xmax = {xmax}\n"
                            f"Ymin = {ymin}\n"
                            f"Ymax = {ymax}\n"
                        )
                        det = local_tracker.parse_yolo_output(yolo_block)
                        if det is not None:
                            motion = local_tracker.compute_motion(det)
                    else:
                        local_tracker.mark_no_detection()

                    # Update shared runtime motion value for STATUS queries
                    runtime["vision_motion"] = motion

                    # Determine threat and tick FSM
                    is_threat = motion >= float(cfg.vision.motion_threshold)
                    fsm.tick(
                        enable=bool(runtime["enable"]),
                        threat=is_threat,
                        motion_magnitude=motion,
                        radar_distance_m=None,  # radar handled in main loop
                        now=time.monotonic(),
                    )

                    obj_count = len(detections)
                    print(
                        f"[CAMERA] frame={frame_index} objects={obj_count} "
                        f"motion={motion:.1f} threat={is_threat} "
                        f"fsm={fsm.state.name}",
                        flush=True,
                    )

            except Exception as e:
                import traceback
                print(f"[CAMERA] Error in camera_loop: {e}", flush=True)
                traceback.print_exc()
            finally:
                cam_state["active"] = False   # ← ensure flag is cleared even on crash
                try:
                    picam2.stop()
                    picam2.close()   # ← this is the critical missing line
                except Exception:
                    pass
                print("[CAMERA] Thread stopped.", flush=True)

        # ===== CAMERA START/STOP HELPERS =====
        def start_camera_thread():
            """Start the camera background thread if not already running."""
            if cam_state["active"]:
                print("[CAMERA] Already running, skipping start.", flush=True)
                return
            cam_state["active"] = True
            t = threading.Thread(target=camera_loop, daemon=True)
            cam_state["thread"] = t
            t.start()
            print("[CAMERA] Thread launched.", flush=True)

        def stop_camera_thread():
            if not cam_state["active"]:
                return
            cam_state["active"] = False
            t = cam_state["thread"]
            if t is not None:
                t.join(timeout=3.0)
                time.sleep(0.3)   # ← let libcamera finish releasing the device
                cam_state["thread"] = None
            print("[CAMERA] Thread joined.", flush=True)

        # ===== BLE COMMAND HANDLER =====
        def handle_ble(cmd) -> str:
            s = cmd.raw.strip()
            if not s:
                return "ERR EMPTY"
            s_up = s.upper()

            print(f"[BLE] cmd={s!r}", flush=True)

            # ----- ENABLE=0/1 -----
            if s_up.startswith("ENABLE="):
                val = s.split("=", 1)[1].strip()
                if val not in ("0", "1"):
                    return "ERR BAD_VALUE ENABLE"

                prev = runtime["enable"]
                runtime["enable"] = int(val)

                if runtime["enable"] == 1:
                    runtime["enable_armed_at"] = time.monotonic()
                    if radar:
                        radar.reset_baseline()
                    if cfg.vision.enabled:
                        start_camera_thread()
                    logger.log_event(
                        state="BLE_TEST", enable_flag=1,
                        command="ENABLE", result=f"{prev}->1",
                        notes="System enabled",
                        radar_enabled=runtime["radar_enabled"], radar_zone="",
                    )
                else:
                    stop_camera_thread()
                    logger.log_event(
                        state="BLE_TEST", enable_flag=0,
                        command="ENABLE", result=f"{prev}->0",
                        notes="System disabled",
                        radar_enabled=runtime["radar_enabled"], radar_zone="",
                    )

                return f"ACK ENABLE={runtime['enable']}"

            # ----- ACTUATOR=EXTEND/RETRACT -----
            if s_up.startswith("ACTUATOR="):
                val = s.split("=", 1)[1].strip().upper()
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
                    traceback.print_exc()
                    return f"ERR ACTUATOR_FAIL {type(e).__name__}"
                return f"ACK ACTUATOR={val}"

            # ----- RETRACT (manual from FEEDING) -----
            if s_up == "RETRACT":
                success = fsm.manual_retract()
                if success:
                    logger.log_event(
                        state="FEEDING", enable_flag=runtime["enable"],
                        command="RETRACT", result="SUCCESS",
                        notes="Manual retraction from FEEDING state",
                        radar_enabled=runtime["radar_enabled"], radar_zone="",
                    )
                    return "ACK RETRACT"
                return "ERR RETRACT not_in_feeding_state"

            # ----- VISION= (CSV detection line from external source) -----
            if s_up.startswith("VISION="):
                if not cfg.vision.enabled or vision_tracker is None:
                    return "ERR VISION_DISABLED"
                detection_line = s.split("=", 1)[1].strip()
                try:
                    det = vision_tracker.parse_line(detection_line)
                    if det is None:
                        return "ERR VISION_PARSE_FAILED"
                    motion = vision_tracker.compute_motion(det)
                    if sensor_fusion:
                        sensor_fusion.update_vision(det.timestamp)
                    runtime["vision_motion"] = motion
                    return f"ACK VISION motion={motion:.2f}"
                except Exception as e:
                    return f"ERR VISION_EXCEPTION {type(e).__name__}"

            # ----- SET key=value -----
            if s_up.startswith("SET "):
                rest = s[4:].strip()
                if "=" not in rest:
                    return "ERR BAD_FORMAT SET"
                key, value = [x.strip() for x in rest.split("=", 1)]
                key_l = key.lower()
                if key_l not in runtime or key_l == "enable":
                    return f"ERR UNKNOWN_KEY {key}"

                def to_bool01(v):
                    if v not in ("0", "1"):
                        raise ValueError
                    return v == "1"

                try:
                    if key_l == "stillness_threshold":
                        f = float(value)
                        if f > 1.0 and f <= 100.0:
                            f /= 100.0
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
                    state="BLE_TEST", enable_flag=runtime["enable"],
                    command=f"SET {key_l}", result=str(runtime[key_l]),
                    notes="Config parameter set (not persisted)",
                    radar_enabled=runtime["radar_enabled"], radar_zone="",
                )
                return f"ACK {key_l}={runtime[key_l]}"

            # ----- GET key -----
            if s_up.startswith("GET "):
                key_l = s[4:].strip().lower()
                if key_l not in runtime:
                    return f"ERR UNKNOWN_KEY {key_l}"
                return f"ACK {key_l}={runtime[key_l]}"

            # ----- STATUS -----
            if s_up in ("GET STATUS", "STATUS"):
                vision_status = ""
                if cfg.vision.enabled:
                    vision_status = (
                        f" vision_enabled=1"
                        f" vision_motion={runtime['vision_motion']:.2f}"
                        f" fused_threat={runtime['fused_threat']}"
                        f" camera_active={int(cam_state['active'])}"
                    )
                return (
                    f"ACK STATUS "
                    f"enable={runtime['enable']} "
                    f"feeder_state={runtime['feeder_state']} "
                    f"retract_delay_ms={runtime['retract_delay_ms']} "
                    f"pulse_ms={runtime['pulse_ms']} "
                    f"radar_enabled={int(runtime['radar_enabled'])}"
                    f"{vision_status}"
                )

            return "ERR UNKNOWN_CMD"

        # ===== START BLE =====
        ble.set_command_handler(handle_ble)
        threading.Thread(target=ble.start, daemon=True).start()

        logger.log_event(
            state="BLE_TEST", enable_flag=runtime["enable"],
            command="SESSION", result="START",
            notes=f"BLE test started; config={Path(args.config).as_posix()}",
            radar_enabled=runtime["radar_enabled"], radar_zone="",
        )
        print("BLE test mode running. Send newline-terminated commands.", flush=True)
        print(f"Session log: {log_path}", flush=True)

        # ===== MAIN CONTROL LOOP =====
        try:
            tick_hz = 20.0
            tick_dt = 1.0 / tick_hz
            last_radar_seq = -1
            BLE_TIMEOUT_S = 30.0

            while True:
                # BLE inactivity safety timeout
                if runtime["ble_disconnect_safe_idle"] and runtime["enable"] == 1:
                    if (time.time() - ble.last_rx_time) > BLE_TIMEOUT_S:
                        runtime["enable"] = 0
                        stop_camera_thread()
                        logger.log_event(
                            state="BLE_TEST", enable_flag=0,
                            command="SAFETY", result="BLE_TIMEOUT->ENABLE=0",
                            notes="Forced safe idle due to BLE inactivity",
                            radar_enabled=runtime["radar_enabled"], radar_zone="",
                        )
                        ble.notify("ACK ENABLE=0\n")

                # Radar threat detection (only in LURE state, after arming delay)
                threat = False
                radar_zone = ""
                RADAR_ARM_DELAY_S = 1.5
                fsm_state_name = getattr(fsm.state, "name", str(fsm.state))

                radar_allowed = (
                    runtime["enable"] == 1
                    and runtime["radar_enabled"]
                    and (time.monotonic() - runtime["enable_armed_at"]) >= RADAR_ARM_DELAY_S
                    and fsm_state_name == "LURE"
                )

                radar_distance_m = None

                if radar and radar_allowed:
                    rr = radar.get_latest()
                    if rr.valid and rr.seq != last_radar_seq:
                        last_radar_seq = rr.seq
                        radar_zone = str(rr.bin_index) if rr.bin_index is not None else ""
                        threat = rr.threat
                        radar_distance_m = rr.distance_m
                        print(
                            f"[RADAR] seq={rr.seq} bin={rr.bin_index} "
                            f"dist={rr.distance_m:.2f}m threat={rr.threat}",
                            flush=True,
                        )
                        if sensor_fusion:
                            sensor_fusion.update_radar(rr.timestamp)

                # Debug: always show latest radar reading regardless of gate
                if radar:
                    rr_debug = radar.get_latest()
                    if rr_debug.valid:
                        print(
                            f"[RADAR] dist={rr_debug.distance_m:.2f}m "
                            f"bin={rr_debug.bin_index} threat={rr_debug.threat} "
                            f"seq={rr_debug.seq} allowed={radar_allowed}",
                            flush=True,
                        )

                # Sensor fusion (radar + vision)
                # Note: camera thread ticks FSM for vision; main loop handles radar-only tick
                # when camera is NOT running, to ensure FSM still gets regular ticks.
                motion_magnitude = runtime.get("vision_motion", 0.0)
                fused_threat = threat

                if sensor_fusion:
                    fused_threat = sensor_fusion.fused_threat(threat, motion_magnitude, radar_distance_m)
                    runtime["fused_threat"] = int(fused_threat)

                # Only tick FSM from main loop when camera thread is NOT running,
                # to avoid double-ticking which can cause state machine race conditions.
                if not cam_state["active"]:
                    fsm.tick(
                        enable=bool(runtime["enable"]),
                        threat=fused_threat,
                        motion_magnitude=motion_magnitude if cfg.vision.enabled else None,
                        radar_distance_m=radar_distance_m,
                        now=time.monotonic(),
                    )

                # Track FSM state changes for logging
                new_state = getattr(fsm.state, "name", str(fsm.state))
                if new_state != runtime["feeder_state"]:
                    print(f"[FSM] {runtime['feeder_state']} -> {new_state}", flush=True)
                runtime["feeder_state"] = new_state
                runtime["radar_last_bin"] = radar_zone
                runtime["radar_threat"] = int(threat)

                time.sleep(tick_dt)

        except KeyboardInterrupt:
            stop_camera_thread()
            logger.log_event(
                state="BLE_TEST", enable_flag=runtime["enable"],
                command="SESSION", result="STOP",
                notes="BLE test stopped by user",
                radar_enabled=runtime["radar_enabled"], radar_zone="",
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
    enable_flag = 1
    state = "ENABLED"
    session_id = make_session_id()
    log_dir = pick_log_dir(cfg.logging.log_dir)
    log_path = log_dir / f"session_{session_id}.csv"
    logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
    logger.open()

    logger.log_event(
        state=state, enable_flag=enable_flag,
        command="enable_flag", result="0->1",
        notes=f"Session start; config={Path(args.config).as_posix()}",
        radar_enabled=cfg.radar.enabled, radar_zone="",
    )

    telemetry_hz = float(cfg.logging.telemetry_hz)
    period_s = 1.0 / telemetry_hz
    still_f = 0.0
    alpha = 0.2

    t_end = time.time() + float(args.demo_seconds)
    while time.time() < t_end:
        still_raw = random.random()
        still_f = (1 - alpha) * still_f + alpha * still_raw
        logger.log_telemetry(
            state=state, enable_flag=enable_flag,
            stillness_raw=still_raw, stillness_filtered=still_f,
            radar_enabled=cfg.radar.enabled,
            radar_zone="DISABLED" if not cfg.radar.enabled else "UNKNOWN",
        )
        time.sleep(period_s)

    enable_flag = 0
    state = "IDLE"
    logger.log_event(
        state=state, enable_flag=enable_flag,
        command="enable_flag", result="1->0",
        notes="Session end (demo complete)",
        radar_enabled=cfg.radar.enabled, radar_zone="",
    )
    logger.close()
    print(f"Wrote session log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
