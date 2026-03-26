"""
Feeder Finite State Machine (FSM) Module

This module implements a state machine that controls the Polar Feeder's behavior based on:
- Enable/disable signals (user control)
- Threat detection (motion sensors detecting predators)
- Timing constraints (delays between actions)

State Flow:
    IDLE -> LURE -> (threat detected) -> RETRACT_WAIT -> COOLDOWN -> IDLE

The FSM ensures safe, controlled behavior:
- Food is only extended when the feeder is enabled
- The feeder retracts immediately upon detecting threats
- A cooldown period prevents rapid cycles that could jam or damage the mechanism
- All transitions are driven by the tick() method called at regular intervals
"""

import time
from enum import Enum, auto


class State(Enum):
    """
    States of the feeder state machine.
    
    IDLE:
        The feeder is inactive. The arm is retracted. No lure is extended.
        - Transition to LURE when enabled
        - Return to IDLE when disabled
    
    LURE:
        The feeder arm is extended to lure animals.
        - Stays in LURE as long as enabled and no threat detected
        - Transition to RETRACT_WAIT when a threat is detected
        - Return to IDLE if disabled
    
    RETRACT_WAIT:
        Waiting for the retract_delay to elapse before retracting the arm.
        - This delay allows the animal to grab the food before we pull it away
        - After delay expires, transitions to COOLDOWN
    
    COOLDOWN:
        The arm is retracted and we're waiting before re-extending.
        - Prevents rapid extend/retract cycles that could damage the mechanism
        - After cooldown expires, resets to IDLE and allows re-extending
    """
    IDLE = auto()
    LURE = auto()
    RETRACT_WAIT = auto()
    COOLDOWN = auto()


class FeederFSM:
    """
    Finite State Machine for controlling the Polar Feeder.
    
    This class orchestrates the feeder behavior through discrete states and transitions.
    It's driven by a tick() method that should be called regularly (e.g., every 100ms)
    with the current enable and threat status.
    
    Key Features:
    - Safe disable-to-idle transition (retracts immediately if disabled)
    - Threat-triggered retraction with configurable delay
    - Cooldown period to prevent mechanism damage
    - Time-based state transitions using deadlines
    
    Example Usage:
        feeder = FeederFSM(actuator, retract_delay_ms=500, cooldown_s=2.0)
        
        while True:
            threat_detected = motion_sensor.detect()
            feeder_enabled = user_control.is_enabled()
            feeder.tick(enable=feeder_enabled, threat=threat_detected)
            time.sleep(0.1)  # Call tick ~10 times per second
    """
    
    def __init__(self, actuator, retract_delay_ms: int, cooldown_s: float = 2.0):
        """
        Initialize the feeder FSM.
        
        Args:
            actuator: An Actuator object with extend() and retract() methods
            retract_delay_ms: Delay in milliseconds between extending the lure and retracting it.
                            This allows the animal time to grab the food. Range: 0-3000ms typical.
            cooldown_s: Delay in seconds before allowing another extend cycle after retraction.
                       Prevents rapid cycling that could damage the mechanism. Default: 2.0s
        """
        self.actuator = actuator
        # Convert milliseconds to seconds and ensure non-negative
        self.retract_delay_s = max(0.0, retract_delay_ms / 1000.0)
        self.cooldown_s = max(0.0, cooldown_s)

        # Current state of the FSM
        self.state = State.IDLE
        # Deadline for transitioning to the next state (when now >= deadline, transition occurs)
        self._deadline = None
        # Flag to track if we've extended the lure in this IDLE period
        # Prevents multiple extends during a single enable period
        self._lure_extended_once = False

    def _set_state(self, s: State, deadline=None):
        """
        Internal method to transition to a new state.
        
        This is the single point of state transition, ensuring consistency across the FSM.
        
        Args:
            s: The new State to transition to
            deadline: Optional float (from time.monotonic()) when this state should transition to the next.
                     If None, no automatic deadline-based transition will occur.
        """
        self.state = s
        self._deadline = deadline

    def tick(self, enable: bool, threat: bool, now: float | None = None):
        """
        Process one cycle of the state machine.
        
        This method should be called regularly (e.g., every 100ms) with the current
        enable and threat status. It handles all state transitions and actuator commands.
        
        Args:
            enable: Boolean indicating if the feeder is enabled (user control)
            threat: Boolean indicating if a threat is detected (motion sensor)
            now: Optional timestamp from time.monotonic(). If None, uses current time.
                 Useful for testing with simulated time.
        
        Control Flow:
        1. If disabled: Immediately retract and go to IDLE (safety first)
        2. If enabled:
           - IDLE: Extend the lure and transition to LURE
           - LURE: Wait for threat detection
           - RETRACT_WAIT: Wait for retract_delay, then retract and go to COOLDOWN
           - COOLDOWN: Wait for cooldown_s, then reset to IDLE
        """
        now = now if now is not None else time.monotonic()

        # ===== DISABLE PATH: Safety first =====
        # If disabled, immediately retract and go to safe IDLE state
        if not enable:
            if self.state != State.IDLE:
                self.actuator.retract()  # Ensure arm is retracted
            self._lure_extended_once = False  # Reset for next enable cycle
            self._set_state(State.IDLE, deadline=None)
            return

        # ===== ENABLE PATH: Normal operation =====
        # IDLE state: Ready to dispense, so extend the lure
        if self.state == State.IDLE:
            if not self._lure_extended_once:
                self.actuator.extend()  # Send RF signal to extend the arm
                self._lure_extended_once = True  # Mark that we've extended this cycle
            self._set_state(State.LURE, deadline=None)  # Wait for threat or disable
            return

        # LURE state: Food is extended, waiting for animal or threat
        if self.state == State.LURE:
            if threat:
                # Threat detected! Start the retract delay
                # The retract_delay gives the animal time to grab the food
                self._set_state(State.RETRACT_WAIT, deadline=now + self.retract_delay_s)
            return

        # RETRACT_WAIT state: Waiting for retract_delay before pulling the food back
        if self.state == State.RETRACT_WAIT:
            # Safety: ensure deadline is set (shouldn't be needed, but defensive coding)
            if self._deadline is None:
                self._deadline = now + self.retract_delay_s
            # Once deadline reached, retract the arm
            if now >= self._deadline:
                self.actuator.retract()  # Send RF signal to retract the arm
                # Now start the cooldown period before allowing another cycle
                self._set_state(State.COOLDOWN, deadline=now + self.cooldown_s)
            return

        # COOLDOWN state: Arm retracted, waiting before next cycle to avoid damage
        if self.state == State.COOLDOWN:
            # Safety: ensure deadline is set (shouldn't be needed, but defensive coding)
            if self._deadline is None:
                self._deadline = now + self.cooldown_s
            # Once cooldown deadline reached, reset to IDLE for next cycle
            if now >= self._deadline:
                self._lure_extended_once = False  # Reset flag to allow extending again
                self._set_state(State.IDLE, deadline=None)  # Ready to dispense again
            return
