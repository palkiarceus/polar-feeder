"""
Inverse Feeder Finite State Machine (FSM) Module — INVERSE mode

State Flow:
    IDLE -> WATCHING -> (bear still >= min_still_duration_s) -> DISPENSING
                     -> (bear moves or leaves)               -> COOLDOWN -> WATCHING

INVERSE mode rewards stillness: arm stays retracted until the bear holds
completely still, then extends as a reward. Any movement retracts it again.

Key differences from LURE mode:
- motion_threshold here means "max motion to be considered STILL" (lower = stricter)
- stillness_min_duration_s is the hold time before food extends
- noise_buffer_multiplier: motion must exceed threshold * multiplier to BREAK
  stillness once DISPENSING has started — prevents single noisy frames from
  resetting the reward unfairly
"""

import time
from enum import Enum, auto


class InverseState(Enum):
    """
    States of the INVERSE feeder state machine.

    IDLE:       System just enabled. Transitions immediately to WATCHING.
    WATCHING:   Arm retracted. Accumulating stillness timer.
                Bear must be detected and hold still for min_still_duration_s.
    DISPENSING: Arm extended. Bear is being rewarded.
                Held until bear moves (motion > threshold * noise_buffer_multiplier)
                or leaves frame entirely.
    COOLDOWN:   Arm retracted. Brief pause before watching again.
    """
    IDLE = auto()
    WATCHING = auto()
    DISPENSING = auto()
    COOLDOWN = auto()


class InverseFeederFSM:
    """
    Inverse FSM: rewards polar bear stillness with food.

    Args:
        actuator:                 Actuator with extend() and retract() methods.
        motion_threshold:         Max pixel motion to be considered "still".
                                  Separate from LURE threshold — lower = stricter.
        min_still_duration_s:     How long bear must hold still before food extends.
        cooldown_s:               Wait time after retraction before watching again.
        detection_distance_m:     Max radar distance for bear to be "in game".
        feeding_distance_m:       Min radar distance (bear must be at least this close).
        noise_buffer_multiplier:  Motion must exceed threshold * this value to break
                                  DISPENSING. Prevents single jitter frames from
                                  resetting the reward. Default 1.5 (50% buffer).
    """

    def __init__(
        self,
        actuator,
        motion_threshold: float = 20.0,
        min_still_duration_s: float = 1.5,
        cooldown_s: float = 2.0,
        detection_distance_m: float = 3.0,
        feeding_distance_m: float = 0.5,
        noise_buffer_multiplier: float = 1.5,
    ):
        self.actuator = actuator
        self.motion_threshold = motion_threshold
        self.min_still_duration_s = max(0.0, min_still_duration_s)
        self.cooldown_s = max(0.0, cooldown_s)
        self.detection_distance_m = detection_distance_m
        self.feeding_distance_m = feeding_distance_m
        self.noise_buffer_multiplier = max(1.0, noise_buffer_multiplier)

        self.state = InverseState.IDLE
        self._deadline = None
        self._still_since: float | None = None
        self._dispensing = False

    def _set_state(self, s: InverseState, deadline=None):
        self.state = s
        self._deadline = deadline

    def _retract_if_extended(self):
        """Retract arm only if currently extended — avoids redundant RF pulses."""
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
            enable:           System enabled flag.
            bear_detected:    True if YOLO detected bear this frame.
            motion_magnitude: Vision motion score in pixels (None = unknown).
            radar_distance_m: Current radar distance in metres (None = unknown).
            now:              Monotonic timestamp override (for testing).
        """
        now = now if now is not None else time.monotonic()

        # Safety: disable always wins
        if not enable:
            self._retract_if_extended()
            self._still_since = None
            self._set_state(InverseState.IDLE)
            return

        # IDLE: transition immediately to WATCHING
        if self.state == InverseState.IDLE:
            self._retract_if_extended()
            self._still_since = None
            self._set_state(InverseState.WATCHING)
            return

        # WATCHING: accumulate stillness timer, extend when threshold met
        if self.state == InverseState.WATCHING:
            bear_in_range = True
            if radar_distance_m is not None:
                bear_in_range = (
                    self.feeding_distance_m
                    <= radar_distance_m
                    <= self.detection_distance_m
                )

            # Treat unknown motion as infinite (can't confirm stillness without data)
            motion = motion_magnitude if motion_magnitude is not None else float('inf')
            bear_is_still = (
                bear_detected
                and motion < self.motion_threshold
                and bear_in_range
            )

            if bear_is_still:
                if self._still_since is None:
                    self._still_since = now
                    print("[INVERSE FSM] Bear still — started timer", flush=True)

                elapsed = now - self._still_since
                print(
                    f"[INVERSE FSM] WATCHING still={elapsed:.1f}s"
                    f" / {self.min_still_duration_s:.1f}s needed"
                    f" motion={motion:.1f}",
                    flush=True,
                )

                if elapsed >= self.min_still_duration_s:
                    self.actuator.extend()
                    self._dispensing = True
                    self._still_since = None
                    self._set_state(InverseState.DISPENSING)
                    print("[INVERSE FSM] WATCHING -> DISPENSING (stillness met)", flush=True)
            else:
                if self._still_since is not None:
                    print(
                        f"[INVERSE FSM] WATCHING stillness broken"
                        f" (motion={motion:.1f} threshold={self.motion_threshold:.1f})",
                        flush=True,
                    )
                self._still_since = None
            return

        # DISPENSING: hold food extended while bear stays still
        # Use noise_buffer_multiplier to avoid single jitter frames breaking the reward
        if self.state == InverseState.DISPENSING:
            motion = motion_magnitude if motion_magnitude is not None else float('inf')
            break_threshold = self.motion_threshold * self.noise_buffer_multiplier
            bear_is_still = bear_detected and (motion < break_threshold)

            if not bear_is_still:
                reason = "moved" if bear_detected else "left frame"
                print(
                    f"[INVERSE FSM] DISPENSING -> COOLDOWN"
                    f" (bear {reason} motion={motion:.1f}"
                    f" break_threshold={break_threshold:.1f})",
                    flush=True,
                )
                self._retract_if_extended()
                self._set_state(InverseState.COOLDOWN, deadline=now + self.cooldown_s)
            else:
                print(
                    f"[INVERSE FSM] DISPENSING bear still"
                    f" motion={motion:.1f} / break_threshold={break_threshold:.1f}",
                    flush=True,
                )
            return

        # COOLDOWN: wait before watching again
        if self.state == InverseState.COOLDOWN:
            if self._deadline is None:
                self._deadline = now + self.cooldown_s
            if now >= self._deadline:
                self._set_state(InverseState.WATCHING)
                print("[INVERSE FSM] COOLDOWN done -> WATCHING", flush=True)
            return

    def manual_retract(self, now: float | None = None) -> bool:
        """
        Manually retract from DISPENSING state (operator override).

        Returns True if retraction was performed, False if not in DISPENSING.
        Transitions to COOLDOWN rather than WATCHING to give the bear a reset gap.
        """
        if self.state != InverseState.DISPENSING:
            return False
        now = now if now is not None else time.monotonic()
        self._retract_if_extended()
        self._set_state(InverseState.COOLDOWN, deadline=now + self.cooldown_s)
        print("[INVERSE FSM] Manual retract from DISPENSING -> COOLDOWN", flush=True)
        return True
