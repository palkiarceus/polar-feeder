# feeder_fsm.py
import time
from enum import Enum, auto

class State(Enum):
    IDLE = auto()
    LURE = auto()
    RETRACT_WAIT = auto()
    COOLDOWN = auto()

class FeederFSM:
    def __init__(self, actuator, retract_delay_ms: int, cooldown_s: float = 2.0):
        self.actuator = actuator
        self.retract_delay_s = max(0.0, retract_delay_ms / 1000.0)
        self.cooldown_s = max(0.0, cooldown_s)

        self.state = State.IDLE
        self._deadline = None
        self._lure_extended_once = False

    def _set_state(self, s: State, deadline=None):
        self.state = s
        self._deadline = deadline

    def tick(self, enable: bool, threat: bool, now: float | None = None):
        now = now if now is not None else time.monotonic()

        # Disable => safe idle
        if not enable:
            if self.state != State.IDLE:
                self.actuator.retract()
            self._lure_extended_once = False
            self._set_state(State.IDLE, deadline=None)
            return

        # Enable path
        if self.state == State.IDLE:
            if not self._lure_extended_once:
                self.actuator.extend()
                self._lure_extended_once = True
            self._set_state(State.LURE, deadline=None)
            return

        if self.state == State.LURE:
            if threat:
                self._set_state(State.RETRACT_WAIT, dea_
