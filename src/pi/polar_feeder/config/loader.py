"""Configuration loader for the Polar Feeder application.

This module converts a JSON config file into type-safe dataclasses with validation.
It ensures missing keys are reported clearly and numeric ranges are clamped to safe values.

Config class hierarchy:
  - LureConfig:    LURE mode FSM parameters
  - InverseConfig: INVERSE mode FSM parameters
  - LoggingConfig: telemetry/event logging behavior
  - RadarConfig:   threat sensor connection and sensitivity
  - SafetyConfig:  safety features (e.g., BLE disconnect behavior)
  - ActuatorConfig: motion control timing parameters (shared physical params)
  - VisionConfig:  camera/vision processing parameters
  - AppConfig:     root config object carrying all sub-configs
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class LureConfig:
    motion_threshold: float       # px — base threshold at detection_distance_m
    retract_delay_ms: int         # ms to hold food extended after threat detected
    cooldown_s: float             # s to wait after retraction before next cycle


@dataclass(frozen=True)
class InverseConfig:
    motion_threshold: float           # px — max motion to be considered "still"
    stillness_min_duration_s: float   # s bear must hold still before food extends
    noise_buffer_multiplier: float    # multiplier on threshold to break DISPENSING
    cooldown_s: float                 # s to wait after retraction before watching again


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
    detection_distance_m: float


@dataclass(frozen=True)
class SafetyConfig:
    ble_disconnect_safe_idle: bool


@dataclass(frozen=True)
class ActuatorConfig:
    pulse_ms: int
    feeding_distance_m: float


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool
    sync_window_s: float


@dataclass(frozen=True)
class AppConfig:
    lure: LureConfig
    inverse: InverseConfig
    logging: LoggingConfig
    radar: RadarConfig
    safety: SafetyConfig
    actuator: ActuatorConfig
    vision: VisionConfig


def _require(d: Dict[str, Any], key: str) -> Any:
    """Return d[key] or raise ValueError with a clear message."""
    if key not in d:
        raise ValueError(f"Missing required config key: {key}")
    return d[key]


def _clamp_num(name: str, val: float, lo: float, hi: float) -> float:
    """Validate that val is within [lo, hi] inclusive, else raise ValueError."""
    if not (lo <= val <= hi):
        raise ValueError(f"{name} out of range [{lo}, {hi}]: {val}")
    return val


def load_config(config_path: str) -> AppConfig:
    """Load JSON config and return AppConfig with validated values."""
    p = Path(config_path)
    raw = json.loads(p.read_text(encoding="utf-8"))

    lure_raw    = _require(raw, "lure")
    inverse_raw = _require(raw, "inverse")
    log_raw     = _require(raw, "logging")
    radar_raw   = _require(raw, "radar")
    safety_raw  = _require(raw, "safety")
    act_raw     = _require(raw, "actuator")
    vision_raw  = _require(raw, "vision")

    lure_cfg = LureConfig(
        motion_threshold=_clamp_num(
            "lure.motion_threshold", float(_require(lure_raw, "motion_threshold")), 5.0, 200.0
        ),
        retract_delay_ms=int(_clamp_num(
            "lure.retract_delay_ms", float(_require(lure_raw, "retract_delay_ms")), 0, 3000
        )),
        cooldown_s=_clamp_num(
            "lure.cooldown_s", float(_require(lure_raw, "cooldown_s")), 0.0, 30.0
        ),
    )

    inverse_cfg = InverseConfig(
        motion_threshold=_clamp_num(
            "inverse.motion_threshold", float(_require(inverse_raw, "motion_threshold")), 5.0, 200.0
        ),
        stillness_min_duration_s=_clamp_num(
            "inverse.stillness_min_duration_s",
            float(_require(inverse_raw, "stillness_min_duration_s")),
            0.5, 10.0,
        ),
        noise_buffer_multiplier=_clamp_num(
            "inverse.noise_buffer_multiplier",
            float(_require(inverse_raw, "noise_buffer_multiplier")),
            1.0, 3.0,
        ),
        cooldown_s=_clamp_num(
            "inverse.cooldown_s", float(_require(inverse_raw, "cooldown_s")), 0.0, 30.0
        ),
    )

    logging_cfg = LoggingConfig(
        enabled=bool(_require(log_raw, "enabled")),
        telemetry_hz=_clamp_num(
            "logging.telemetry_hz", float(_require(log_raw, "telemetry_hz")), 1, 30
        ),
        max_storage_mb=_clamp_num(
            "logging.max_storage_mb", float(_require(log_raw, "max_storage_mb")), 10, 5000
        ),
        log_dir=str(_require(log_raw, "log_dir")),
    )

    radar_cfg = RadarConfig(
        enabled=bool(_require(radar_raw, "enabled")),
        port=str(_require(radar_raw, "port")),
        baud=int(_clamp_num(
            "radar.baud", float(_require(radar_raw, "baud")), 1200, 2_000_000
        )),
        timeout_s=_clamp_num(
            "radar.timeout_s", float(_require(radar_raw, "timeout_s")), 0.0, 5.0
        ),
        zone_m=[float(x) for x in list(_require(radar_raw, "zone_m"))],
        distance_jump_m=_clamp_num(
            "radar.distance_jump_m", float(_require(radar_raw, "distance_jump_m")), 0.01, 5.0
        ),
        detection_distance_m=_clamp_num(
            "radar.detection_distance_m",
            float(_require(radar_raw, "detection_distance_m")),
            0.5, 50.0,
        ),
    )

    safety_cfg = SafetyConfig(
        ble_disconnect_safe_idle=bool(_require(safety_raw, "ble_disconnect_safe_idle")),
    )

    actuator_cfg = ActuatorConfig(
        pulse_ms=int(_clamp_num(
            "actuator.pulse_ms", float(_require(act_raw, "pulse_ms")), 50, 1000
        )),
        feeding_distance_m=_clamp_num(
            "actuator.feeding_distance_m",
            float(_require(act_raw, "feeding_distance_m")),
            0.1, 5.0,
        ),
    )

    vision_cfg = VisionConfig(
        enabled=bool(_require(vision_raw, "enabled")),
        sync_window_s=_clamp_num(
            "vision.sync_window_s", float(_require(vision_raw, "sync_window_s")), 0.1, 5.0
        ),
    )

    return AppConfig(
        lure=lure_cfg,
        inverse=inverse_cfg,
        logging=logging_cfg,
        radar=radar_cfg,
        safety=safety_cfg,
        actuator=actuator_cfg,
        vision=vision_cfg,
    )
