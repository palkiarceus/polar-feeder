"""Configuration loader for the Polar Feeder application.

This module converts a JSON config file into type-safe dataclasses with validation.
It ensures missing keys are reported clearly and numeric ranges are clamped to safe values.

Config class hierarchy:
  - StillnessConfig: stillness sensor thresholds and publish cadence
  - LoggingConfig: telemetry/event logging behavior
  - RadarConfig: threat sensor connection and sensitivity
  - SafetyConfig: safety features (e.g., BLE disconnect behavior)
  - ActuatorConfig: motion control timing parameters
  - AppConfig: root config object carrying all sub-configs
"""

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
    port: str
    baud: int
    timeout_s: float
    zone_m: list[float]
    distance_jump_m: float


@dataclass(frozen=True)
class SafetyConfig:
    ble_disconnect_safe_idle: bool


@dataclass(frozen=True)
class ActuatorConfig:
    retract_delay_ms: int
    pulse_ms: int


@dataclass(frozen=True)
class AppConfig:
    stillness: StillnessConfig
    logging: LoggingConfig
    radar: RadarConfig
    safety: SafetyConfig
    actuator: ActuatorConfig


def _require(d: Dict[str, Any], key: str) -> Any:
    """Return d[key] or raise ValueError with clear message."""
    if key not in d:
        raise ValueError(f"Missing required config key: {key}")
    return d[key]


def _clamp_num(name: str, val: float, lo: float, hi: float) -> float:
    """Validate that val is within [lo, hi] inclusive else raise ValueError."""
    if not (lo <= val <= hi):
        raise ValueError(f"{name} out of range [{lo}, {hi}]: {val}")
    return val


def load_config(config_path: str) -> AppConfig:
    """Load JSON config and return AppConfig with validated values."""
    p = Path(config_path)
    raw = json.loads(p.read_text(encoding="utf-8"))

    still = _require(raw, "stillness")
    log = _require(raw, "logging")
    radar = _require(raw, "radar")
    safety = _require(raw, "safety")
    act = _require(raw, "actuator")

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
        port=str(_require(radar, "port")),
        baud=int(_clamp_num("radar.baud", float(_require(radar, "baud")), 1200, 2_000_000)),
        timeout_s=_clamp_num("radar.timeout_s", float(_require(radar, "timeout_s")), 0.0, 5.0),
        zone_m=[float(x) for x in list(_require(radar, "zone_m"))],
        distance_jump_m=_clamp_num(
            "radar.distance_jump_m",
            float(_require(radar, "distance_jump_m")),
            0.01,
            5.0,
        ),
    )

    safety_cfg = SafetyConfig(
        ble_disconnect_safe_idle=bool(_require(safety, "ble_disconnect_safe_idle")),
    )

    actuator_cfg = ActuatorConfig(
        retract_delay_ms=int(_clamp_num("actuator.retract_delay_ms", float(_require(act, "retract_delay_ms")), 0, 3000)),
        pulse_ms=int(_clamp_num("actuator.pulse_ms", float(_require(act, "pulse_ms")), 50, 1000)),
    )

    return AppConfig(
        stillness=stillness,
        logging=logging,
        radar=radar_cfg,
        safety=safety_cfg,
        actuator=actuator_cfg,
    )
