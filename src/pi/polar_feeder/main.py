import argparse
import random
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path
import threading

from polar_feeder.config.loader import load_config
from polar_feeder.logging.csv_logger import CsvSessionLogger, pick_log_dir

# BLE + Actuator + FSM (your local files)
from polar_feeder.ble_interface import BleServer
from polar_feeder.actuator import Actuator
from polar_feeder.feeder_fsm import FeederFSM


def make_session_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Polar feeder controller.")
    parser.add_argument("--config", default="config/config.example.json", help="Path to JSON config.")
    parser.add_argument("--test-id", default="", help="Optional test_id to include in logs.")
    parser.add_argument("--demo-seconds", type=float, default=10.0, help="How long to run demo loop.")
    parser.add_argument("--ble-test", action="store_true", help="Start BLE GATT server and handle commands.")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # ---------- BLE TEST MODE ----------
    if args.ble_test:
        session_id = make_session_id()
        log_dir = pick_log_dir(cfg.logging.log_dir)
        log_path = log_dir / f"session_{session_id}.csv"
        logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id=args.test_id)
        logger.open()

        # Runtime values (not persisted)
        runtime = {
            "enable": 0,
            "ble_disconnect_safe_idle": bool(cfg.safety.ble_disconnect_safe_idle),
            "retract_delay_ms": int(cfg.actuator.retract_delay_ms),
            "pulse_ms": int(cfg.actuator.pulse_ms),
            "radar_enabled": bool(cfg.radar.enabled),
            "feeder_state": "IDLE",
        }

        # === GPIO pins (BCM) ===
        # You chose GPIO2 and GPIO3 (pins 3 and 5). Change here if you rewire.
        EXTEND_GPIO = 2
        RETRACT_GPIO = 3

        # Setup actuator + FSM
        act = Actuator(
            extend_line=EXTEND_GPIO,
            retract_line=RETRACT_GPIO,
            pulse_s=runtime["pulse_ms"] / 1000.0,
        )
        act.open()

        fsm = FeederFSM(
            actuator=act,
            retract_delay_ms=runtime["retract_delay_ms"],
            cooldown_s=2.0,   # tweak later
        )

        ble = BleServer(name="PolarFeeder")

        def handle_ble(cmd) -> str:
            s = cmd.raw.strip()
            if not s:
                return "ERR EMPTY"
            s_up = s.upper()

            # ENABLE=0/1
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
                    notes="Runtime enable toggle",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK ENABLE={runtime['enable']}"

            # Manual pulse for testing: ACTUATOR=EXTEND / ACTUATOR=RETRACT
            if s_up.startswith("ACTUATOR="):
                val = s.split("=", 1)[1].strip().upper()
                if val not in ("EXTEND", "RETRACT"):
                    return "ERR BAD_VALUE ACTUATOR"

                try:
                    if val == "EXTEND":
                        act.extend(duration_s=runtime["pulse_ms"] / 1000.0)
                    else:
                        act.retract(duration_s=runtime["pulse_ms"] / 1000.0)
                except Exception as e:
                    return f"ERR ACTUATOR_FAIL {type(e).__name__}"

                logger.log_event(
                    state="BLE_TEST",
                    enable_flag=runtime["enable"],
                    command="ACTUATOR",
                    result=val,
                    notes="Manual actuator pulse",
                    radar_enabled=runtime["radar_enabled"],
                    radar_zone="",
                )
                return f"ACK ACTUATOR={val}"

            # STATUS
            if s_up in ("STATUS", "GET STATUS"):
                return (
                    "ACK STATUS "
                    f"enable={runtime['enable']} "
                    f"feeder_state={runtime['feeder_state']} "
                    f"retract_delay_ms={runtime['retract_delay_ms']} "
                    f"pulse_ms={runtime['pulse_ms']}"
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
        tick_hz = 20.0
        tick_dt = 1.0 / tick_hz

        print("BLE test mode running. Send commands like ENABLE=1 or ACTUATOR=EXTEND", flush=True)
        print(f"Session log: {log_path}", flush=True)

        try:
            while True:
                # Safety: if BLE inactive, force disable
                if runtime["ble_disconnect_safe_idle"] and runtime["enable"] == 1:
                    if (time.time() - ble.last_rx_time) > BLE_TIMEOUT_S:
                        runtime["enable"] = 0
                        ble.notify("ACK ENABLE=0\n")
                        logger.log_event(
                            state="BLE_TEST",
                            enable_flag=runtime["enable"],
                            command="SAFETY",
                            result="BLE_TIMEOUT->ENABLE=0",
                            notes="Forced safe idle due to BLE inactivity timeout",
                            radar_enabled=runtime["radar_enabled"],
                            radar_zone="",
                        )

                # Threat stub for now
                threat = False

                # Tick FSM
                fsm.tick(enable=bool(runtime["enable"]), threat=threat)
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

    # ---------- DEMO LOGGING MODE (your original) ----------
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
        command="enable_flag",
        result="0->1",
        notes=f"Session start; config={Path(args.config).as_posix()}",
        radar_enabled=cfg.radar.enabled,
        radar_zone="",
    )

    telemetry_hz = float(cfg.logging.telemetry_hz)
    period_s = 1.0 / telemetry_hz

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
