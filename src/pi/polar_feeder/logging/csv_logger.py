"""
CSV Logging Module

Provides session-based CSV logging for Polar Feeder telemetry and events.
"""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class CsvSessionLogger:
    log_path: Path
    session_id: str
    test_id: str = ""
    _writer: Optional[csv.DictWriter] = None
    _fh: Optional[Any] = None

    def open(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.log_path.exists()
        self._fh = self.log_path.open("a", newline="", encoding="utf-8")

        fieldnames = [
            # --- Identity ---
            "timestamp_utc",
            "session_id",
            "test_id",
            "event_type",           # 'event' or 'telemetry'

            # --- FSM State ---
            "state",
            "fsm_mode",             # 'LURE' or 'INVERSE'
            "enable_flag",

            # --- Vision ---
            "frame_index",          # Camera frame counter
            "obj_count",            # YOLO detections this frame
            "bear_detected",        # 0/1 (obj_count > 0 and camera active)
            "vision_motion",        # Raw motion magnitude float
            "vision_threat",        # 0/1 (motion >= threshold)
            "camera_active",        # 0/1

            # --- Radar ---
            "radar_dist_m",         # Float distance in meters
            "radar_threat",         # 0/1
            "radar_enabled",        # 0/1
            "radar_zone",           # Bin index string

            # --- Fusion ---
            "fused_threat",         # 0/1

            # --- Tunable Params (snapshot at log time) ---
            "motion_threshold",     # Current live value
            "retract_delay_ms",     # Current live value
            "still_min_dur_s",      # Current live value

            # --- Override ---
            "manual_override_active",  # 0/1

            # --- Telemetry (legacy fields kept for compatibility) ---
            "stillness_raw",
            "stillness_filtered",

            # --- Event fields ---
            "command",
            "result",
            "fault_code",
            "notes",
        ]
        self._writer = csv.DictWriter(self._fh, fieldnames=fieldnames)
        if is_new:
            self._writer.writeheader()
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.flush()
            self._fh.close()
        self._fh = None
        self._writer = None

    def _write(self, row: Dict[str, Any]) -> None:
        if not self._writer or not self._fh:
            raise RuntimeError("Logger not opened")
        self._writer.writerow(row)
        self._fh.flush()

    def _base_row(self) -> Dict[str, Any]:
        """Returns a row with all fields defaulted to empty string."""
        return {
            "timestamp_utc": iso_now(),
            "session_id": self.session_id,
            "test_id": self.test_id,
            "event_type": "",
            "state": "",
            "fsm_mode": "",
            "enable_flag": "",
            "frame_index": "",
            "obj_count": "",
            "bear_detected": "",
            "vision_motion": "",
            "vision_threat": "",
            "camera_active": "",
            "radar_dist_m": "",
            "radar_threat": "",
            "radar_enabled": "",
            "radar_zone": "",
            "fused_threat": "",
            "motion_threshold": "",
            "retract_delay_ms": "",
            "still_min_dur_s": "",
            "manual_override_active": "",
            "stillness_raw": "",
            "stillness_filtered": "",
            "command": "",
            "result": "",
            "fault_code": "",
            "notes": "",
        }

    def log_event(
        self,
        *,
        state: str,
        enable_flag: int,
        command: str = "",
        result: str = "",
        fault_code: str = "",
        notes: str = "",
        radar_enabled: bool = False,
        radar_zone: str = "",
        fsm_mode: str = "",
    ) -> None:
        row = self._base_row()
        row.update({
            "event_type": "event",
            "state": state,
            "fsm_mode": fsm_mode,
            "enable_flag": enable_flag,
            "radar_enabled": int(bool(radar_enabled)),
            "radar_zone": radar_zone,
            "command": command,
            "result": result,
            "fault_code": fault_code,
            "notes": notes,
        })
        self._write(row)

    def log_telemetry(
        self,
        *,
        state: str,
        enable_flag: int,
        fsm_mode: str = "",
        frame_index: int = 0,
        obj_count: int = 0,
        bear_detected: int = 0,
        vision_motion: float = 0.0,
        vision_threat: int = 0,
        camera_active: int = 0,
        radar_dist_m: Optional[float] = None,
        radar_threat: int = 0,
        radar_enabled: bool = False,
        radar_zone: str = "",
        fused_threat: int = 0,
        motion_threshold: float = 0.0,
        retract_delay_ms: int = 0,
        still_min_dur_s: float = 0.0,
        manual_override_active: int = 0,
        stillness_raw: float = 0.0,
        stillness_filtered: float = 0.0,
        notes: str = "",
    ) -> None:
        row = self._base_row()
        row.update({
            "event_type": "telemetry",
            "state": state,
            "fsm_mode": fsm_mode,
            "enable_flag": enable_flag,
            "frame_index": frame_index,
            "obj_count": obj_count,
            "bear_detected": bear_detected,
            "vision_motion": f"{vision_motion:.3f}",
            "vision_threat": vision_threat,
            "camera_active": camera_active,
            "radar_dist_m": f"{radar_dist_m:.2f}" if radar_dist_m is not None else "",
            "radar_threat": radar_threat,
            "radar_enabled": int(bool(radar_enabled)),
            "radar_zone": radar_zone,
            "fused_threat": fused_threat,
            "motion_threshold": f"{motion_threshold:.1f}",
            "retract_delay_ms": retract_delay_ms,
            "still_min_dur_s": f"{still_min_dur_s:.2f}",
            "manual_override_active": manual_override_active,
            "stillness_raw": f"{stillness_raw:.3f}",
            "stillness_filtered": f"{stillness_filtered:.3f}",
            "notes": notes,
        })
        self._write(row)


def pick_log_dir(preferred: str) -> Path:
    pref = Path(preferred)
    try:
        pref.mkdir(parents=True, exist_ok=True)
        testfile = pref / ".write_test"
        testfile.write_text("ok", encoding="utf-8")
        testfile.unlink(missing_ok=True)
        return pref
    except Exception:
        fallback = Path("logs")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
