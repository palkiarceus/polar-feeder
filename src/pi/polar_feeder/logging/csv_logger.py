import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class CsvSessionLogger:
    """
    One CSV per session.
    Combined telemetry + events, differentiated by event_type.
    """
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
            "timestamp_utc",
            "session_id",
            "test_id",
            "event_type",
            "state",
            "enable_flag",
            "stillness_raw",
            "stillness_filtered",
            "radar_enabled",
            "radar_zone",
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
    ) -> None:
        self._write({
            "timestamp_utc": iso_now(),
            "session_id": self.session_id,
            "test_id": self.test_id,
            "event_type": "event",
            "state": state,
            "enable_flag": enable_flag,
            "stillness_raw": "",
            "stillness_filtered": "",
            "radar_enabled": int(bool(radar_enabled)),
            "radar_zone": radar_zone,
            "command": command,
            "result": result,
            "fault_code": fault_code,
            "notes": notes,
        })

    def log_telemetry(
        self,
        *,
        state: str,
        enable_flag: int,
        stillness_raw: float,
        stillness_filtered: float,
        radar_enabled: bool = False,
        radar_zone: str = "",
        notes: str = "",
    ) -> None:
        self._write({
            "timestamp_utc": iso_now(),
            "session_id": self.session_id,
            "test_id": self.test_id,
            "event_type": "telemetry",
            "state": state,
            "enable_flag": enable_flag,
            "stillness_raw": f"{stillness_raw:.3f}",
            "stillness_filtered": f"{stillness_filtered:.3f}",
            "radar_enabled": int(bool(radar_enabled)),
            "radar_zone": radar_zone,
            "command": "",
            "result": "",
            "fault_code": "",
            "notes": notes,
        })


def pick_log_dir(preferred: str) -> Path:
    """
    Try preferred dir first (from config). If not writable, fall back to ./logs.
    Works on Windows + Pi.
    """
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
