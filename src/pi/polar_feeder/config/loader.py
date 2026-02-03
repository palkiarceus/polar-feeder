import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class StillnessConfig:
    publish_hz: float
    trigger_threshold: float
    min_duration_s: float


@dataclass(frozen=True)
class LoggingConfig:
    enabled: bool
    telemetry_hz: float
    max_storage_mb: float
    log_dir: str


@dataclass(frozen=True)
class RadarConfig:
    enabled: bool
    zone_m: list


@dataclass(frozen=True)
class SafetyConfig:
    ble_disconnect_safe_idle: bool


@dataclass(frozen=True)
class AppConfig:
    stillness: StillnessConfig
    logging: LoggingConfig
    radar: RadarConfig
    safety: SafetyConfig


def _require(d: Dict[str, Any], key: str) -> Any:
    if key not in d:
        raise ValueError(f"Missing required config key: {key}")
    return d[key]


def _clamp_num(name: str, val: float, lo: float, hi: float) -> float:
    if not (lo <= val <= hi):
        raise ValueError(f"{name} out of range [{lo}, {hi}]: {val}")
    return val


def load_config(config_path: str) -> AppConfig:
    """
    Load and validate JSON config.
    Raises ValueError on invalid config to support safe fallback behavior.
    """
    p = Path(config_path)
    raw = json.loads(p.read_text(encoding="utf-8"))

    still = _require(raw, "stillness")
    log = _require(raw, "logging")
    radar = _require(raw, "radar")
    safety = _require(raw, "safety")

    stillness = StillnessConfig(
        publish_hz=_clamp_num("stillness.publish_hz", float(_require(still, "publish_hz")), 1, 30),
        trigger_threshold=_clamp_num("stillness.trigger_threshold", float(_require(still, "trigger_threshold")), 0, 1),
        min_duration_s=_clamp_num("stillness.min_duration_s", float(_require(still, "min_duration_s")), 0, 60),
    )

    logging = LoggingConfig(
        enabled=bool(_require(log, "enabled")),
        telemetry_hz=_clamp_num("logging.telemetry_hz", float(_require(log, "telemetry_hz")), 1, 30),
        max_storage_mb=_clamp_num("logging.max_storage_mb", float(_require(log, "max_storage_mb")), 10, 5000),
        log_dir=str(_require(log, "log_dir")),
    )

    radar_cfg = RadarConfig(
        enabled=bool(_require(radar, "enabled")),
        zone_m=list(_require(radar, "zone_m")),
    )

    safety_cfg = SafetyConfig(
        ble_disconnect_safe_idle=bool(_require(safety, "ble_disconnect_safe_idle")),
    )

    return AppConfig(stillness=stillness, logging=logging, radar=radar_cfg, safety=safety_cfg)
