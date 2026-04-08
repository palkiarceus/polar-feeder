
"""
Inverse Feeder Finite State Machine (FSM) Module

This module implements the INVERSE feeding strategy:
- Default state: arm RETRACTED
- Extend food ONLY when bear is detected AND holding still
- Retract immediately if bear moves or disappears

State Flow:
    IDLE -> WATCHING -> (bear detected + still) -> DISPENSING -> (bear moves/leaves) -> COOLDOWN -> IDLE

The inverse FSM rewards stillness:
- Bear must be detected by YOLO (in frame)
- Bear must be holding still (motion below threshold) for min_still_duration_s
- Food extends as reward
- Any movement or loss of detection retracts food
- Cooldown prevents rapid cycling
"""

import time
from enum import Enum, auto


class InverseState(Enum):
    """
    States of the inverse feeder state machine.

    IDLE:
        System enabled but not yet watching. Arm retracted.
        Transitions immediately to WATCHING.

    WATCHING:
        Arm retracted. Watching for bear to appear and hold still.
        - No bear detected: stay in WATCHING
        - Bear detected but moving: stay in WATCHING, reset still timer
        - Bear detected AND still for min_still_duration_s: -> DISPENSING

    DISPENSING:
        Arm extended. Bear is being rewarded for stillness.
        - Bear still detected AND still: stay in DISPENSING
        - Bear moves (motion >= threshold): -> COOLDOWN
        - Bear leaves frame (no detection): -> COOLDOWN

    COOLDOWN:
        Arm retracted. Brief pause before watching again.
        - After cooldown_s: -> WATCHING
    """
    IDLE = auto()
    WATCHING = auto()
    DISPENSING = auto()
    COOLDOWN = auto()


class InverseFeederFSM:
    """
    Inverse Finite State Machine: rewards bear stillness with food.

    Instead of extending food as a lure and retracting on movement,
    this FSM keeps food retracted until the bear holds still, then
    extends as a reward. Movement retracts the food.

    Args:
        actuator: Actuator object with extend() and retract() methods
        motion_threshold: Max pixel displacement considered "still". Below this = still.
        min_still_duration_s: How long bear must be still before food extends.
        cooldown_s: How long to wait after retraction before watching again.
        detection_distance_m: Max radar distance to consider bear "in game".
        feeding_distance_m: Min radar distance (bear must be this close to activate).
    """

    def __init__(
        self,
        actuator,
        motion_threshold: float = 20.0,
        min_still_duration_s: float = 2.0,
        cooldown_s: float = 2.0,
        detection_distance_m: float = 3.0,
        feeding_distance_m: float = 0.5,
    ):
        self.actuator = actuator
        self.motion_threshold = motion_threshold
        self.min_still_duration_s = max(0.0, min_still_duration_s)
        self.cooldown_s = max(0.0, cooldown_s)
        self.detection_distance_m = detection_distance_m
        self.feeding_distance_m = feeding_distance_m

        self.state = InverseState.IDLE
        self._deadline = None          # Used for cooldown and still timers
        self._still_since: float | None = None   # Timestamp when stillness began
        self._dispensing = False       # Track if arm is currently extended

    def _set_state(self, s: InverseState, deadline=None):
        self.state = s
        self._deadline = deadline

    def _retract_if_extended(self):
        """Retract arm only if it was extended, to avoid redundant RF pulses."""
        if self._dispensing:
            self.actuator.retract()
            self._dispensing = False

    def tick(
        self,
        enable: bool,
        bear_detected: bool,
        motion_magnitude: float | None = None,
        radar_distance_m: float | None = None,
        now: float | None = None,
    ):
        """
        Process one FSM cycle.

        Args:
            enable: System enabled flag (from BLE)
            bear_detected: True if YOLO detected bear this inference frame
            motion_magnitude: Bounding box center displacement in pixels (None = unknown)
            radar_distance_m: Current radar distance in meters (None = unknown)
            now: Current monotonic timestamp (uses time.monotonic() if None)
        """
        now = now if now is not None else time.monotonic()

        # ===== DISABLE PATH =====
        if not enable:
            self._retract_if_extended()
            self._still_since = None
            self._set_state(InverseState.IDLE)
            return

        # ===== IDLE: transition immediately to WATCHING =====
        if self.state == InverseState.IDLE:
            self._retract_if_extended()
            self._still_since = None
            self._set_state(InverseState.WATCHING)
            return

        # ===== WATCHING: wait for bear to be still =====
        if self.state == InverseState.WATCHING:
            # Radar gate: if radar says bear is too far or too close, ignore
            bear_in_range = True
            if radar_distance_m is not None:
                bear_in_range = self.feeding_distance_m <= radar_distance_m <= self.detection_distance_m

            # Determine if bear is currently still
            motion = motion_magnitude if motion_magnitude is not None else float('inf')
            bear_is_still = bear_detected and (motion < self.motion_threshold) and bear_in_range

            if bear_is_still:
                # Start or continue counting stillness duration
                if self._still_since is None:
                    self._still_since = now
                    print(f"[INVERSE FSM] Bear still — started timer", flush=True)

                elapsed_still = now - self._still_since
                print(
                    f"[INVERSE FSM] WATCHING still={elapsed_still:.1f}s "
                    f"/ {self.min_still_duration_s:.1f}s needed "
                    f"motion={motion:.1f}",
                    flush=True,
                )

                if elapsed_still >= self.min_still_duration_s:
                    # Bear has been still long enough — extend food as reward
                    self.actuator.extend()
                    self._dispensing = True
                    self._still_since = None
                    self._set_state(InverseState.DISPENSING)
            else:
                # Bear moved, left frame, or out of range — reset timer
                if self._still_since is not None:
                    print(f"[INVERSE FSM] WATCHING stillness broken — resetting timer", flush=True)
                self._still_since = None
            return

        # ===== DISPENSING: hold food extended while bear stays still =====
        if self.state == InverseState.DISPENSING:
            motion = motion_magnitude if motion_magnitude is not None else float('inf')
            bear_is_still = bear_detected and (motion < self.motion_threshold)

            if not bear_is_still:
                # Bear moved or left — retract and cooldown
                reason = "moved" if bear_detected else "left frame"
                print(f"[INVERSE FSM] DISPENSING -> COOLDOWN (bear {reason})", flush=True)
                self._retract_if_extended()
                self._set_state(InverseState.COOLDOWN, deadline=now + self.cooldown_s)
            else:
                print(
                    f"[INVERSE FSM] DISPENSING bear still motion={motion:.1f}",
                    flush=True,
                )
            return

        # ===== COOLDOWN: wait before watching again =====
        if self.state == InverseState.COOLDOWN:
            if self._deadline is None:
                self._deadline = now + self.cooldown_s
            if now >= self._deadline:
                self._set_state(InverseState.WATCHING)
                print(f"[INVERSE FSM] COOLDOWN done -> WATCHING", flush=True)
            return

    def manual_retract(self, now: float | None = None) -> bool:
        """
        Manually retract from DISPENSING state (operator override).

        Returns True if retraction was performed, False if not in DISPENSING.
        """
        if self.state != InverseState.DISPENSING:
            return False
        now = now if now is not None else time.monotonic()
        self._retract_if_extended()
        self._set_state(InverseState.COOLDOWN, deadline=now + self.cooldown_s)
        return True
