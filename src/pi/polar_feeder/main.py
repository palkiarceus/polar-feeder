import argparse
import random
import time
import uuid
from datetime import datetime
from pathlib import Path

from polar_feeder.config.loader import load_config
from polar_feeder.logging.csv_logger import CsvSessionLogger, pick_log_dir


def make_session_id() -> str:
    # e.g., 20260202T213045Z_ab12cd34
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Polar feeder controller (CDR logging/config proof).")
    parser.add_argument("--config", default="config/config.example.json", help="Path to JSON config.")
    parser.add_argument("--test-id", default="", help="Optional test_id to include in logs.")
    parser.add_argument("--demo-seconds", type=float, default=10.0, help="How long to run demo loop.")
    args = parser.parse_args()

    # Load config (validated)
    cfg = load_config(args.config)

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
