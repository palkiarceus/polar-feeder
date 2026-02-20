# main.py
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


def make_session_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Polar feeder controller (CDR logging/config proof).")
    parser.add_argument("--config", default="config/config.example.json", help="Path to JSON config.")
    parser.add_argument("--test-id", default="", help="Optional test_id to include in logs.")
    parser.add_argument("--demo-seconds", type=float, default=10.0, help="How long to run demo loop.")
    parser.add_argument("--ble-test", action="store_true", help="Start BLE GATT server and handle commands.")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # ---------- BLE TEST MODE ----------
    if args.ble_test:
        # Local imports so demo mode doesn't require these modules
        from polar_feeder.actuator import Actuator
        from polar_feeder.feeder_fsm import FeederFSM

        session_id = make_session_id()
        log_dir = pick_log_dir(cfg.logging.log_dir)
        log_path = log_dir / f"session_{session_id}.csv"
        logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
        logger.open()

        # Runtime parameters that can be changed via BLE (not persisted)
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

            # Actuator params
            "retract_delay_ms": int(cfg.actuator.retract_delay_ms),
            "pulse_ms": int(cfg.actuator.pulse_ms),

            # For status reporting
            "actuator_cmd": "IDLE",
            "feeder_state": "IDLE",
        }

        # === GPIO pins (BCM) ===
        # You said you plan GPIO2 and GPIO3 (physical pins 3 and 5).
        # NOTE: These are I2C SDA/SCL pins. If you see weird behavior, disable I2C or switch pins.
        EXTEND_GPIO = 17
        RETRACT_GPIO = 27

        # Create actuator + FSM
        act = Actuator(
            extend_line=EXTEND_GPIO,
            retract_line=RETRACT_GPIO,
            pulse_s=runtime["pulse_ms"] / 1000.0,
        )
        act.open()

        # Cooldown is not in config yet; choose something safe for testing
        COOLDOWN_S = 2.0
        fsm = FeederFSM(
            actuator=act,
            retract_delay_ms=runtime["retract_delay_ms"],
            cooldown_s=COOLDOWN_S,
        )

        ble = BleServer(name="PolarFeeder")

        def handle_ble(cmd) -> str:
            s = cmd.raw.strip()
            if not s:
                return "ERR EMPTY"
            s_up = s.upper()

            # ENABLE=0/1 controls the session and drives the FSM
            if s_up.startswith("ENABLE="):
                val = s.split("=", 1)[1].strip()
                if val not in ("0", "1"):
                    return "ERR BAD_VALUE ENABLE"
                prev = runtime["enable"]
                runtime["enable"] = int(val)

                logger.log_event(
                    state="BLE_TEST",
                    enable_flag=runtime["enable"],
                    command="ENABLE",
                    result=f"{prev}->{runtime['enable']}",
                    notes="Runtime enable toggle (not persisted)",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK ENABLE={runtime['enable']}"

            # Manual actuator pulse (debug/testing): ACTUATOR=EXTEND / ACTUATOR=RETRACT
            if s_up.startswith("ACTUATOR="):
                val = s.split("=", 1)[1].strip().upper()
                if val not in ("EXTEND", "RETRACT"):
                    return "ERR BAD_VALUE ACTUATOR"

                runtime["actuator_cmd"] = val

                # Pulse immediately
                try:
                    if val == "EXTEND":
                        act.extend(duration_s=runtime["pulse_ms"] / 1000.0)
                    else:
                        act.retract(duration_s=runtime["pulse_ms"] / 1000.0)
                except Exception as e:
                    logger.log_event(
                        state="BLE_TEST",
                        enable_flag=runtime["enable"],
                        command="ACTUATOR",
                        result="ERROR",
                        notes=f"Manual actuator command failed: {type(e).__name__}",
                        radar_enabled=runtime["radar_enabled"],
                        radar_zone="",
                    )
                    return f"ERR ACTUATOR_FAIL {type(e).__name__}"

                logger.log_event(
                    state="BLE_TEST",
                    enable_flag=runtime["enable"],
                    command="ACTUATOR",
                    result=val,
                    notes="Manual actuator command (pulsed GPIO)",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK ACTUATOR={val}"

            # SET key=value for runtime parameters (not persisted)
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
                        # Update FSM live
                        fsm.retract_delay_s = n / 1000.0

                    elif key_l == "pulse_ms":
                        n = int(value)
                        if not (50 <= n <= 1000):
                            return "ERR OUT_OF_RANGE pulse_ms 50 1000"
                        runtime[key_l] = n
                        # Update actuator pulse default if your actuator supports it
                        try:
                            act.pulse_s = n / 1000.0  # if your Actuator stores pulse_s
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
                # Keep response compact and readable
                return (
                    "ACK STATUS "
                    f"enable={runtime['enable']} "
                    f"feeder_state={runtime['feeder_state']} "
                    f"retract_delay_ms={runtime['retract_delay_ms']} "
                    f"pulse_ms={runtime['pulse_ms']} "
                    f"radar_enabled={int(runtime['radar_enabled'])}"
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

        BLE_TIMEOUT_S = 5.0
        print("BLE test mode running. Send newline-terminated commands (end with \\n).", flush=True)
        print(f"Session log: {log_path}", flush=True)

        try:
            # Run FSM at a steady tick rate
            tick_hz = 20.0
            tick_dt = 1.0 / tick_hz

            while True:
                # BLE inactivity safety
                if runtime["ble_disconnect_safe_idle"] and runtime["enable"] == 1:
                    if (time.time() - ble.last_rx_time) > BLE_TIMEOUT_S:
                        runtime["enable"] = 0
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

                # Threat stub for now (later: compute from CV/radar/stillness)
                threat = False

                # Tick the state machine
                fsm.tick(enable=bool(runtime["enable"]), threat=threat)

                # Report state for STATUS queries
                runtime["feeder_state"] = getattr(fsm.state, "name", str(fsm.state))

                time.sleep(tick_dt)

        except KeyboardInterrupt:
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
                act.close()
            except Exception:
                pass
            logger.close()
            print("BLE test stopped.", flush=True)
            return 0

    # ---------- DEMO LOGGING MODE (unchanged) ----------
    # Session concept: enable_flag transitions 0->1 starts
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

    telemetry_hz = float(cfg.logging.telemetry_hz)
    period_s = 1.0 / telemetry_hz

    # Simulated stillness: raw in [0,1], filtered is a simple low-pass
    still_f = 0.0

    t_end = time.time() + float(args.demo_seconds)
    while time.time() < t_end:
        still_raw = random.random()
        alpha = 0.2
        still_f = (1 - alpha) * still_f + alpha * still_raw

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
    raise SystemExit(main())
