"""
Inverse Feeder Finite State Machine (FSM) Module — INVERSE mode


State Flow:
    IDLE -> WATCHING -> (bear still >= min_still_duration_s)          -> DISPENSING
                     -> (bear moves or leaves)                         -> COOLDOWN -> WATCHING

    DISPENSING -> (bear still + close >= reward_hold_s) -> REWARDING -> (manual retract) -> COOLDOWN
               -> (bear moves or leaves)                -> COOLDOWN -> WATCHING

INVERSE mode rewards stillness: arm stays retracted until the bear holds
completely still, then extends as a reward. If the bear holds still AND
is close enough for reward_hold_s, it locks into REWARDING (arm stays
extended until manual retract). Any movement during DISPENSING retracts
the arm immediately.
""

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
    IDLE:       System just enabled. Transitions immediately to WATCHING.
    WATCHING:   Arm retracted. Accumulating stillness timer.
    DISPENSING: Arm extended. Bear held still long enough but not yet close.
                Retracts immediately on movement.
    REWARDING:  Arm extended indefinitely. Bear earned full reward by being
                still AND close for reward_hold_s. Only manual_retract() exits.
    COOLDOWN:   Arm retracted. Brief pause before watching again.
    """
    IDLE = auto()
    WATCHING = auto()
    DISPENSING = auto()
    REWARDING = auto()
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
        self.reward_hold_s = min_still_duration_s   # ← here
        self._dispensing_since: float | None = None  # ← here

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

            # Only reset the timer if motion exceeds threshold * noise_buffer_multiplier.
            # A single jitter frame above bare threshold no longer kills the stillness streak.
            break_threshold = self.motion_threshold * self.noise_buffer_multiplier
            timer_should_reset = (
                not bear_detected
                or motion >= break_threshold
                or not bear_in_range
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
            elif timer_should_reset:
                if self._still_since is not None:
                    print(
                        f"[INVERSE FSM] WATCHING stillness broken"
                        f" (motion={motion:.1f} break_threshold={break_threshold:.1f})",
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

            # Bear must also be close enough to earn the full reward lock
            bear_is_close = (
                radar_distance_m is not None
                and radar_distance_m <= self.feeding_distance_m
            )

            if bear_is_still:
                if self._dispensing_since is None:
                    self._dispensing_since = now
                elapsed = now - self._dispensing_since

                print(
                    f"[INVERSE FSM] DISPENSING still={elapsed:.1f}s"
                    f" / {self.reward_hold_s:.1f}s to REWARDING"
                    f" motion={motion:.1f}"
                    f" close={bear_is_close}"
                    f" radar={radar_distance_m}",
                    flush=True,
                )

                if elapsed >= self.reward_hold_s and bear_is_close:
                    self._set_state(InverseState.REWARDING)
                    print("[INVERSE FSM] DISPENSING -> REWARDING (still + close)", flush=True)
                elif elapsed >= self.reward_hold_s and not bear_is_close:
                    # Still held long enough but not close — stay in DISPENSING, reset timer
                    # so bear has to re-earn it once it approaches
                    self._dispensing_since = now
                    print(
                        f"[INVERSE FSM] DISPENSING hold met but bear too far"
                        f" ({radar_distance_m}m > {self.feeding_distance_m}m)"
                        f" — resetting timer",
                        flush=True,
                    )
            else:
                reason = "moved" if bear_detected else "left frame"
                print(
                    f"[INVERSE FSM] DISPENSING -> COOLDOWN"
                    f" (bear {reason} motion={motion:.1f}"
                    f" break_threshold={break_threshold:.1f})",
                    flush=True,
                )
                self._dispensing_since = None
                self._retract_if_extended()
                self._set_state(InverseState.COOLDOWN, deadline=now + self.cooldown_s)
            return
        if self.state == InverseState.REWARDING:
            # Arm stays extended indefinitely — only manual_retract() exits
            print("[INVERSE FSM] REWARDING — holding extended, awaiting manual retract", flush=True)
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
        if self.state not in (InverseState.DISPENSING, InverseState.REWARDING):
            return False
        now = now if now is not None else time.monotonic()
        self._dispensing_since = None
        self._retract_if_extended()
        self._set_state(InverseState.COOLDOWN, deadline=now + self.cooldown_s)
        print(f"[INVERSE FSM] Manual retract -> COOLDOWN", flush=True)
        return True
