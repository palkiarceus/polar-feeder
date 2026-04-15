"""
Feeder Finite State Machine (FSM) Module — LURE mode

State Flow:
    IDLE -> LURE -> (threat detected) -> RETRACT_WAIT -> COOLDOWN -> IDLE
                 -> (bear close)      -> FEEDING -> (manual retract) -> IDLE

LURE mode extends food to attract the bear, then retracts when the bear moves
toward it (threat detected). The bear "wins" by getting close enough to eat
before the tray pulls back.
"""

import time
from enum import Enum, auto


class State(Enum):
    """
    States of the LURE feeder state machine.

    IDLE:         Arm retracted. Transitions to LURE immediately on enable.
    LURE:         Arm extended. Waiting for threat or feeding proximity.
    FEEDING:      Bear reached feeding distance. Arm stays extended indefinitely.
                  Only a manual RETRACT command exits this state.
    RETRACT_WAIT: Threat detected. Waiting retract_delay_s before pulling arm back.
    COOLDOWN:     Arm retracted. Waiting cooldown_s before next cycle.
    """
    IDLE = auto()
    LURE = auto()
    FEEDING = auto()
    RETRACT_WAIT = auto()
    COOLDOWN = auto()


class FeederFSM:
    """
    Finite State Machine for LURE mode polar bear feeder control.

    Driven by tick() called from the camera thread (~5-10 Hz).
    All timing uses time.monotonic() deadlines.

    Args:
        actuator:              Actuator with extend() and retract() methods.
        retract_delay_ms:      ms to hold food extended after threat before retracting.
        cooldown_s:            s to wait after retraction before next cycle.
        motion_threshold:      Base pixel motion threshold at detection_distance_m.
                               Scales down (stricter) as bear gets closer.
        feeding_distance_m:    Radar distance at which bear is considered feeding.
        detection_distance_m:  Max radar distance for threat detection to be active.
    """

    def __init__(
        self,
        actuator,
        retract_delay_ms: int,
        cooldown_s: float = 2.0,
        motion_threshold: float = 30.0,
        feeding_distance_m: float = 0.5,
        detection_distance_m: float = 3.0,
    ):
        self.actuator = actuator
        self.retract_delay_s = max(0.0, retract_delay_ms / 1000.0)
        self.cooldown_s = max(0.0, cooldown_s)
        self.base_motion_threshold = max(0.0, motion_threshold)
        self.feeding_distance_m = max(0.0, feeding_distance_m)
        self.detection_distance_m = max(0.0, detection_distance_m)

        self.state = State.IDLE
        self._deadline = None
        self._lure_extended_once = False

    def _set_state(self, s: State, deadline=None):
        self.state = s
        self._deadline = deadline

    def _adaptive_motion_threshold(self, radar_distance_m: float | None) -> float:
        """
        Distance-adaptive motion threshold for LURE mode.

        No radar:  Returns base threshold unchanged (lenient, no distance context).
        Too far:   Returns infinity (bear out of game range, ignore all motion).
        At floor:  Returns base * 0.4 (40% floor — stays above YOLO jitter noise).
        In range:  Square-root interpolation between base and floor.

        sqrt curve: pixel displacement scales ~1/distance, so the threshold
        should drop faster up close than a linear curve would. sqrt() achieves
        this — slow drop at long range, steeper as bear approaches.

        Floor raised from 20% (original) to 40% because zoo data showed mean
        motion of ~12px on a stationary bear at <0.5m, putting the old 4px
        floor well inside the noise band.
        """
        floor = self.base_motion_threshold * 0.4

        if radar_distance_m is None:
            return self.base_motion_threshold

        if radar_distance_m > self.detection_distance_m:
            return float('inf')

        if radar_distance_m <= self.feeding_distance_m:
            return floor

        distance_range = self.detection_distance_m - self.feeding_distance_m
        threshold_range = self.base_motion_threshold - floor
        progress = ((self.detection_distance_m - radar_distance_m) / distance_range) ** 0.5
        adaptive_threshold = self.base_motion_threshold - (progress * threshold_range)

        return max(adaptive_threshold, floor)

    def tick(
        self,
        enable: bool,
        threat: bool,
        motion_magnitude: float | None = None,
        radar_distance_m: float | None = None,
        now: float | None = None,
    ):
        """
        Process one FSM cycle.

        Args:
            enable:           Feeder enabled flag.
            threat:           Radar threat boolean (sudden distance jump).
            motion_magnitude: Vision motion score in pixels (None = unknown).
            radar_distance_m: Current radar distance in metres (None = unknown).
            now:              Monotonic timestamp override (for testing).
        """
        now = now if now is not None else time.monotonic()

        # Safety: disable always wins, retract immediately
        if not enable:
            if self.state != State.IDLE:
                self.actuator.retract()
            self._lure_extended_once = False
            self._set_state(State.IDLE)
            return

        if self.state == State.IDLE:
            if not self._lure_extended_once:
                self.actuator.extend()
                self._lure_extended_once = True
            self._set_state(State.LURE)
            return

        if self.state == State.LURE:
            # Bear close enough to feed — let it eat
            if radar_distance_m is not None and radar_distance_m <= self.feeding_distance_m:
                self._set_state(State.FEEDING)
                return

            # Only process threats when bear is within detection range
            bear_in_game = (
                radar_distance_m is None
                or radar_distance_m <= self.detection_distance_m
            )

            current_threshold = self._adaptive_motion_threshold(radar_distance_m)
            fused_threat = threat and bear_in_game
            if (
                motion_magnitude is not None
                and motion_magnitude >= current_threshold
                and bear_in_game
            ):
                fused_threat = True

            if fused_threat:
                self._set_state(State.RETRACT_WAIT, deadline=now + self.retract_delay_s)
            return

        if self.state == State.FEEDING:
            # Hold indefinitely — only manual_retract() exits this state
            return

        if self.state == State.RETRACT_WAIT:
            if self._deadline is None:
                self._deadline = now + self.retract_delay_s
            if now >= self._deadline:
                self.actuator.retract()
                self._set_state(State.COOLDOWN, deadline=now + self.cooldown_s)
            return

        if self.state == State.COOLDOWN:
            if self._deadline is None:
                self._deadline = now + self.cooldown_s
            if now >= self._deadline:
                self._lure_extended_once = False
                self._set_state(State.IDLE)
            return

    def manual_retract(self, now: float | None = None) -> bool:
        """
        Manually retract from FEEDING state (operator command).

        Returns True if retraction was performed, False if not in FEEDING.
        """
        if self.state != State.FEEDING:
            return False
        now = now if now is not None else time.monotonic()
        self.actuator.retract()
        self._lure_extended_once = False
        self._set_state(State.IDLE)
        return True
