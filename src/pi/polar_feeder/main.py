"""
Main Entry Point for Polar Feeder Controller

Supports two modes:

1. BLE Mode (--ble-test):
   - Starts a BLE GATT server for remote control via Android app
   - On ENABLE=1: starts camera thread running YOLO + FSM
   - On ENABLE=0: stops camera thread cleanly
   - Logs all events and telemetry to CSV
   - Supports MODE=LURE and MODE=INVERSE via BLE command

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
import lgpio
import os

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

    # ===== BLE MODE =====
    if args.ble_test:
        from polar_feeder.actuator import Actuator
        from polar_feeder.feeder_fsm import FeederFSM
        from polar_feeder.inverse_feeder_fsm import InverseFeederFSM

        # ===== SESSION SETUP =====
        session_id = make_session_id()
        log_dir = pick_log_dir(cfg.logging.log_dir)
        log_path = log_dir / f"session_{session_id}.csv"
        logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
        logger.open()

        # ===== RUNTIME STATE =====
        runtime = {
            "enable": 0,
            "fsm_mode": "LURE",
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
            "radar_distance_m": None,
            "radar_fused_threat": False,
            "manual_override_until": 0.0,   # monotonic timestamp; FSM tick suppressed until then
        }

        # ===== CAMERA THREAD STATE =====
        cam_state = {
            "active": False,
            "thread": None,
        }

        # ===== INITIALIZE ACTUATOR =====
        COOLDOWN_S = 2.0
        act = Actuator()
        act.open()

        # ===== FSM FACTORY =====
        def make_fsm():
            if runtime["fsm_mode"] == "INVERSE":
                print("[FSM] Creating INVERSE FSM", flush=True)
                return InverseFeederFSM(
                    actuator=act,
                    motion_threshold=runtime["motion_threshold"],
                    min_still_duration_s=runtime["stillness_min_duration_s"],
                    cooldown_s=COOLDOWN_S,
                    detection_distance_m=runtime["detection_distance_m"],
                    feeding_distance_m=runtime["feeding_distance_m"],
                )
            else:
                print("[FSM] Creating LURE FSM", flush=True)
                return FeederFSM(
                    actuator=act,
                    retract_delay_ms=runtime["retract_delay_ms"],
                    cooldown_s=COOLDOWN_S,
                    motion_threshold=runtime["motion_threshold"],
                    feeding_distance_m=runtime["feeding_distance_m"],
                    detection_distance_m=runtime["detection_distance_m"],
                )

        fsm_holder = [make_fsm()]

        # ===== INITIALIZE BLE SERVER =====
        ble = BleServer(name="PolarFeeder")

        # ===== INITIALIZE RADAR =====
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

        # ===== INITIALIZE VISION =====
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
            from picamera2 import Picamera2
            import cv2
            from ultralytics import YOLO
            modelload = "yolo26n_ncnn_model"
            print("[Model] Model Loaded =", modelload, flush=True)
            print("[CAMERA] Thread starting...", flush=True)

            try:
                model = YOLO(modelload, task="detect")
                local_tracker = VisionTracker()

                # GPIO indicator LED on pin 27 — high when bear detected
                led_h = lgpio.gpiochip_open(0)
                lgpio.gpio_claim_output(led_h, 27)

                picam2 = Picamera2()
                cam_cfg = picam2.create_video_configuration(
                    main={"format": "XRGB8888", "size": (640, 480)}
                )
                picam2.configure(cam_cfg)
                picam2.start()
                print("[CAMERA] PiCamera2 started at 640x480", flush=True)

                frame_index = 0
                SKIP = 2

                while cam_state["active"]:
                    if not cam_state["active"]:
                        break

                    frame_bgra = picam2.capture_array()
                    frame = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
                    frame_index += 1

                    if frame_index % SKIP != 0:
                        continue

                    results = model(frame, classes=[21], verbose=False)

                    dtimestart = time.perf_counter()
                    detections = results[0].boxes
                    dtimeend = time.perf_counter()

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
                    runtime["vision_motion"] = motion

                    obj_count = len(detections)
                    lgpio.gpio_write(led_h, 27, 1 if obj_count > 0 else 0)

                    is_vision_threat = motion >= float(runtime["motion_threshold"])
                    is_radar_threat = runtime["radar_fused_threat"]
                    is_threat = is_vision_threat or is_radar_threat
                    radar_dist = runtime["radar_distance_m"]
                    radar_str = f"{radar_dist:.2f}m" if radar_dist is not None else "None"

                    # ===== DUAL-PATH FSM TICK (respects manual override) =====
                    fsm = fsm_holder[0]
                    prev_state = fsm.state.name
                    override_active = time.monotonic() < runtime["manual_override_until"]

                    if not override_active:
                        if runtime["fsm_mode"] == "INVERSE":
                            fsm.tick(
                                enable=bool(runtime["enable"]),
                                bear_detected=(obj_count > 0),
                                motion_magnitude=motion,
                                radar_distance_m=radar_dist,
                                now=time.monotonic(),
                            )
                        else:
                            try:
                                fsm.tick(
                                    enable=bool(runtime["enable"]),
                                    threat=is_threat,
                                    motion_magnitude=motion,
                                    radar_distance_m=radar_dist,
                                    now=time.monotonic(),
                                )
                            except Exception as e:
                                print(f"[FSM] tick error (non-fatal): {e}", flush=True)
                    else:
                        if frame_index % 20 == 0:
                            print("[MANUAL] FSM tick suppressed (override active)", flush=True)

                    new_state = fsm.state.name

                    # ===== TERMINAL + LOGGING (unchanged) =====
                    print(
                        f"[CAMERA] frame={frame_index} objects={obj_count} "
                        f"motion={motion:.1f} vision_threat={is_vision_threat} "
                        f"radar_threat={is_radar_threat} radar_dist={radar_str} "
                        f"mode={runtime['fsm_mode']} fsm={new_state} "
                        f"override={int(override_active)}",
                        flush=True,
                    )

                    if obj_count > 0:
                        dtime = dtimeend - dtimestart
                        print(f"[CAMERA] detection_time={dtime}s", flush=True)

                    if new_state != prev_state:
                        print(f"[FSM] {prev_state} -> {new_state}", flush=True)
                        logger.log_event(
                            state=new_state,
                            enable_flag=runtime["enable"],
                            fsm_mode=runtime["fsm_mode"],
                            command="FSM_TRANSITION",
                            result=f"{prev_state}->{new_state}",
                            notes=(
                                f"motion={motion:.1f} vision_threat={is_vision_threat} "
                                f"radar_threat={is_radar_threat} radar_dist={radar_str} "
                                f"override={int(override_active)}"
                            ),
                            radar_enabled=runtime["radar_enabled"],
                            radar_zone=runtime["radar_last_bin"],
                        )

                    if frame_index % 20 == 0:
                        logger.log_telemetry(
                            state=new_state,
                            enable_flag=runtime["enable"],
                            fsm_mode=runtime["fsm_mode"],
                            frame_index=frame_index,
                            obj_count=obj_count,
                            bear_detected=int(obj_count > 0),
                            vision_motion=motion,
                            vision_threat=int(is_vision_threat),
                            camera_active=1,
                            radar_dist_m=radar_dist,
                            radar_threat=int(is_radar_threat),
                            radar_enabled=runtime["radar_enabled"],
                            radar_zone=radar_str,
                            fused_threat=runtime["fused_threat"],
                            motion_threshold=float(runtime["motion_threshold"]),
                            retract_delay_ms=int(runtime["retract_delay_ms"]),
                            still_min_dur_s=float(runtime["stillness_min_duration_s"]),
                            manual_override_active=int(override_active),
                            stillness_raw=motion,
                            stillness_filtered=motion,
                        )

            except Exception as e:
                import traceback
                print(f"[CAMERA] Error in camera_loop: {e}", flush=True)
                traceback.print_exc()
            finally:
                cam_state["active"] = False
                try:
                    lgpio.gpio_write(led_h, 27, 0)
                    lgpio.gpiochip_close(led_h)
                except Exception:
                    pass
                try:
                    picam2.stop()
                    picam2.close()
                except Exception:
                    pass
                print("[CAMERA] Thread stopped.", flush=True)
                
        # ===== CAMERA START/STOP HELPERS =====
        def start_camera_thread():
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
                time.sleep(0.3)
                cam_state["thread"] = None
            print("[CAMERA] Thread joined.", flush=True)

        # ===== BLE COMMAND HANDLER =====
        def handle_ble(cmd) -> str:
            s = cmd.raw.strip()

            if not s:
                return "ERR EMPTY"
            s_up = s.upper()

            # ----- PING -----
            if s_up == "PING":
                return f"PONG fsm={runtime['feeder_state']} mode={runtime['fsm_mode']}"
            print(f"[BLE] cmd={s!r}", flush=True)

            # ----- ENABLE=0/1 -----
            if s_up.startswith("ENABLE="):
                val = s.split("=", 1)[1].strip()
                if val not in ("0", "1"):
                    return "ERR BAD_VALUE ENABLE"

                prev = runtime["enable"]
                runtime["enable"] = int(val)

                if runtime["enable"] == 1:
                    fsm_holder[0] = make_fsm()
                    runtime["enable_armed_at"] = time.monotonic()
                    runtime["manual_override_until"] = 0.0  # clear any stale override on fresh enable
                    if radar:
                        radar.reset_baseline()
                    if cfg.vision.enabled:
                        start_camera_thread()
                    logger.log_event(
                        state=runtime["feeder_state"],
                        enable_flag=1,
                        fsm_mode=runtime["fsm_mode"],
                        command="ENABLE",
                        result=f"{prev}->1",
                        notes=f"System enabled mode={runtime['fsm_mode']}",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )
                else:
                    stop_camera_thread()
                    logger.log_event(
                        state=runtime["feeder_state"],
                        enable_flag=0,
                        fsm_mode=runtime["fsm_mode"],
                        command="ENABLE",
                        result=f"{prev}->0",
                        notes="System disabled",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )

                return f"ACK ENABLE={runtime['enable']}"

            # ----- MODE=LURE/INVERSE -----
            if s_up.startswith("MODE="):
                val = s.split("=", 1)[1].strip().upper()
                if val not in ("LURE", "INVERSE"):
                    return "ERR BAD_VALUE MODE must be LURE or INVERSE"
                if val == runtime["fsm_mode"]:
                    return f"ACK MODE={val} (no change)"

                was_enabled = runtime["enable"] == 1
                if was_enabled:
                    print(f"[MODE] Stopping camera for hot-swap to {val}...", flush=True)
                    stop_camera_thread()

                runtime["fsm_mode"] = val
                fsm_holder[0] = make_fsm()
                print(f"[MODE] Switched to {val} FSM", flush=True)

                if was_enabled:
                    start_camera_thread()
                    print(f"[MODE] Camera restarted with {val} FSM", flush=True)

                logger.log_event(
                    state=runtime["feeder_state"],
                    enable_flag=runtime["enable"],
                    fsm_mode=runtime["fsm_mode"],
                    command="MODE",
                    result=val,
                    notes=f"FSM mode hot-swapped to {val} (camera_restart={was_enabled})",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK MODE={val}"

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
                    runtime["manual_override_until"] = time.monotonic() + 5.0
                    print(f"[MANUAL] Actuator override active for 5s", flush=True)
                    logger.log_event(
                        state=runtime["feeder_state"],
                        enable_flag=runtime["enable"],
                        fsm_mode=runtime["fsm_mode"],
                        command=f"ACTUATOR={val}",
                        result="SUCCESS",
                        notes="Manual actuator command; FSM suppressed 5s",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    return f"ERR ACTUATOR_FAIL {type(e).__name__}"
                return f"ACK ACTUATOR={val}"

            # ----- RETRACT (FSM state override) -----
            if s_up == "RETRACT":
                success = fsm_holder[0].manual_retract()
                if success:
                    logger.log_event(
                        state=fsm_holder[0].state.name,
                        enable_flag=runtime["enable"],
                        fsm_mode=runtime["fsm_mode"],
                        command="RETRACT",
                        result="SUCCESS",
                        notes="Manual retraction via FSM",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )
                    return "ACK RETRACT"
                return "ERR RETRACT not_in_retractable_state"

            # ----- VISION= -----
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
            KNOWN_ALIASES = {"mt", "sm", "rd", "motion_thresh", "still_dur_s", "retract_ms"}
            if s_up.startswith("SET "):
                rest = s[4:].strip()
                print(f"[SET DEBUG] s={s!r} rest={rest!r}", flush=True)
                if "=" not in rest:
                    return "ERR BAD_FORMAT SET"
                key, value = [x.strip() for x in rest.split("=", 1)]
                key_l = key.lower()
                if (key_l not in runtime and key_l not in KNOWN_ALIASES) or key_l == "enable":
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
                        runtime["stillness_threshold"] = f

                    elif key_l in ("mt", "motion_thresh", "motion_threshold"):
                        f = float(value)
                        if not (0.0 <= f <= 1000.0):
                            return "ERR OUT_OF_RANGE motion_threshold 0 1000"
                        runtime["motion_threshold"] = f
                        fsm_holder[0].motion_threshold = f
                        if hasattr(fsm_holder[0], 'base_motion_threshold'):
                            fsm_holder[0].base_motion_threshold = f
                        if sensor_fusion:
                            sensor_fusion.base_motion_threshold = f

                    elif key_l in ("sm", "still_dur_s", "stillness_min_duration_s"):
                        f = float(value)
                        if not (0.0 <= f <= 60.0):
                            return "ERR OUT_OF_RANGE stillness_min_duration_s 0 60"
                        runtime["stillness_min_duration_s"] = f
                        if hasattr(fsm_holder[0], 'min_still_duration_s'):
                            fsm_holder[0].min_still_duration_s = f

                    elif key_l in ("rd", "retract_ms", "retract_delay_ms"):
                        n = int(value)
                        if not (0 <= n <= 3000):
                            return "ERR OUT_OF_RANGE retract_delay_ms 0 3000"
                        runtime["retract_delay_ms"] = n
                        if hasattr(fsm_holder[0], 'retract_delay_s'):
                            fsm_holder[0].retract_delay_s = n / 1000.0

                    elif key_l == "detection_distance_m":
                        f = float(value)
                        if not (0.5 <= f <= 50.0):
                            return "ERR OUT_OF_RANGE detection_distance_m 0.5 50"
                        runtime["detection_distance_m"] = f
                        fsm_holder[0].detection_distance_m = f
                        if sensor_fusion:
                            sensor_fusion.detection_distance_m = f

                    elif key_l == "pulse_ms":
                        n = int(value)
                        if not (50 <= n <= 1000):
                            return "ERR OUT_OF_RANGE pulse_ms 50 1000"
                        runtime["pulse_ms"] = n
                        try:
                            act.pulse_s = n / 1000.0
                        except Exception:
                            pass

                    elif key_l == "stillness_publish_hz":
                        f = float(value)
                        if not (1.0 <= f <= 30.0):
                            return "ERR OUT_OF_RANGE stillness_publish_hz 1 30"
                        runtime["stillness_publish_hz"] = f

                    elif key_l == "telemetry_hz":
                        f = float(value)
                        if not (1.0 <= f <= 30.0):
                            return "ERR OUT_OF_RANGE telemetry_hz 1 30"
                        runtime["telemetry_hz"] = f

                    elif key_l == "max_storage_mb":
                        f = float(value)
                        if not (10.0 <= f <= 5000.0):
                            return "ERR OUT_OF_RANGE max_storage_mb 10 5000"
                        runtime["max_storage_mb"] = f

                    elif key_l in ("radar_enabled", "log_enabled", "ble_disconnect_safe_idle"):
                        runtime[key_l] = to_bool01(value)

                    else:
                        runtime[key_l] = value

                except ValueError:
                    return f"ERR TYPE {key} bad_value"

                canonical_map = {
                    "mt": "motion_threshold", "motion_thresh": "motion_threshold",
                    "sm": "stillness_min_duration_s", "still_dur_s": "stillness_min_duration_s",
                    "rd": "retract_delay_ms", "retract_ms": "retract_delay_ms",
                }
                canonical = canonical_map.get(key_l, key_l)
                logger.log_event(
                    state=runtime["feeder_state"],
                    enable_flag=runtime["enable"],
                    fsm_mode=runtime["fsm_mode"],
                    command=f"SET {canonical}",
                    result=str(runtime[canonical]),
                    notes="Config parameter set (not persisted)",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK {canonical}={runtime[canonical]}"

            # ----- GET key -----
            if s_up.startswith("GET "):
                key_l = s[4:].strip().lower()
                if key_l not in runtime:
                    return f"ERR UNKNOWN_KEY {key_l}"
                return f"ACK {key_l}={runtime[key_l]}"

            # ----- STATUS -----
            if s_up.startswith("STATUS"):
                radar_dist_str = f"{runtime['radar_distance_m']:.2f}" if runtime['radar_distance_m'] is not None else "None"
                bear_detected = 1 if (runtime["vision_motion"] > 0 and cam_state["active"]) else 0
                override_active = int(time.monotonic() < runtime["manual_override_until"])
                vision_status = ""
                if cfg.vision.enabled:
                    vision_status = (
                        f" vision_enabled=1"
                        f" vision_motion={runtime['vision_motion']:.2f}"
                        f" fused_threat={runtime['fused_threat']}"
                        f" camera_active={int(cam_state['active'])}"
                        f" bear_detected={bear_detected}"
                    )
                return (
                    f"ACK STATUS "
                    f"enable={runtime['enable']} "
                    f"fsm_mode={runtime['fsm_mode']} "
                    f"feeder_state={runtime['feeder_state']} "
                    f"retract_delay_ms={runtime['retract_delay_ms']} "
                    f"{vision_status} "
                    f"radar_dist={radar_dist_str} "
                    f"override={override_active} "
                )

            return "ERR UNKNOWN_CMD"

        # ===== START BLE =====
        ble.set_command_handler(handle_ble)
        threading.Thread(target=ble.start, daemon=True).start()

        logger.log_event(
            state="IDLE",
            enable_flag=runtime["enable"],
            fsm_mode=runtime["fsm_mode"],
            command="SESSION",
            result="START",
            notes=f"Session started; config={Path(args.config).as_posix()}",
            radar_enabled=runtime["radar_enabled"],
            radar_zone="",
        )
        print("BLE server running. Waiting for commands.", flush=True)
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
                            state=runtime["feeder_state"],
                            enable_flag=0,
                            fsm_mode=runtime["fsm_mode"],
                            command="SAFETY",
                            result="BLE_TIMEOUT->ENABLE=0",
                            notes="Forced safe idle due to BLE inactivity",
                            radar_enabled=runtime["radar_enabled"],
                            radar_zone="",
                        )
                        ble.notify("ACK ENABLE=0\n")

                # Radar reading
                threat = False
                radar_zone = ""
                RADAR_ARM_DELAY_S = 1.5
                fsm_state_name = getattr(fsm_holder[0].state, "name", str(fsm_holder[0].state))

                if runtime["fsm_mode"] == "LURE":
                    radar_allowed = (
                        runtime["enable"] == 1
                        and runtime["radar_enabled"]
                        and (time.monotonic() - runtime["enable_armed_at"]) >= RADAR_ARM_DELAY_S
                        and fsm_state_name == "LURE"
                    )
                else:
                    radar_allowed = (
                        runtime["enable"] == 1
                        and runtime["radar_enabled"]
                        and (time.monotonic() - runtime["enable_armed_at"]) >= RADAR_ARM_DELAY_S
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

                # Sensor fusion
                motion_magnitude = runtime.get("vision_motion", 0.0)
                fused_threat = threat

                if sensor_fusion:
                    fused_threat = sensor_fusion.fused_threat(threat, motion_magnitude, radar_distance_m)
                    runtime["fused_threat"] = int(fused_threat)
                    runtime["radar_fused_threat"] = bool(fused_threat)

                # Keep latest radar distance fresh for camera thread
                if radar:
                    rr_latest = radar.get_latest()
                    if rr_latest.valid:
                        runtime["radar_distance_m"] = rr_latest.distance_m
                        runtime["radar_threat"] = int(rr_latest.threat)

                # Tick FSM from main loop only when camera is not running
                if not cam_state["active"]:
                    override_active = time.monotonic() < runtime["manual_override_until"]
                    if not override_active:
                        fsm = fsm_holder[0]
                        if runtime["fsm_mode"] == "INVERSE":
                            fsm.tick(
                                enable=bool(runtime["enable"]),
                                bear_detected=False,
                                motion_magnitude=None,
                                radar_distance_m=radar_distance_m,
                                now=time.monotonic(),
                            )
                        else:
                            fsm.tick(
                                enable=bool(runtime["enable"]),
                                threat=fused_threat,
                                motion_magnitude=motion_magnitude if cfg.vision.enabled else None,
                                radar_distance_m=radar_distance_m,
                                now=time.monotonic(),
                            )

                # Track FSM state
                new_state = getattr(fsm_holder[0].state, "name", str(fsm_holder[0].state))
                runtime["feeder_state"] = new_state
                runtime["radar_last_bin"] = radar_zone
                runtime["radar_threat"] = int(threat)

                time.sleep(tick_dt)

        except KeyboardInterrupt:
            stop_camera_thread()
            logger.log_event(
                state=runtime["feeder_state"],
                enable_flag=runtime["enable"],
                fsm_mode=runtime["fsm_mode"],
                command="SESSION",
                result="STOP",
                notes="Session stopped by user",
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
            print("Session stopped.", flush=True)
            return 0

    # ===== DEMO MODE =====
    enable_flag = 1
    state = "ENABLED"
    session_id = make_session_id()
    log_dir = pick_log_dir(cfg.logging.log_dir)
    log_path = log_dir / f"session_{session_id}.csv"
    logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
    logger.open()

    logger.log_event(
        state=state,
        enable_flag=enable_flag,
        fsm_mode="DEMO",
        command="SESSION",
        result="START",
        notes=f"Demo session started; config={Path(args.config).as_posix()}",
        radar_enabled=cfg.radar.enabled,
        radar_zone="",
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
            state=state,
            enable_flag=enable_flag,
            fsm_mode="DEMO",
            stillness_raw=still_raw,
            stillness_filtered=still_f,
            radar_enabled=cfg.radar.enabled,
            radar_zone="DISABLED" if not cfg.radar.enabled else "UNKNOWN",
        )
        time.sleep(period_s)

    logger.log_event(
        state="IDLE",
        enable_flag=0,
        fsm_mode="DEMO",
        command="SESSION",
        result="STOP",
        notes="Demo session complete",
        radar_enabled=cfg.radar.enabled,
        radar_zone="",
    )
    logger.close()
    print(f"Wrote session log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
