"""
Feeder Finite State Machine (FSM) Module

This module implements a state machine that controls the Polar Feeder's behavior based on:
- Enable/disable signals (user control)
- Threat detection (motion sensors detecting predators)
- Timing constraints (delays between actions)

State Flow:
    IDLE -> LURE -> (threat detected) -> RETRACT_WAIT -> COOLDOWN -> IDLE
                     -> (bear close) -> FEEDING -> (manual retract) -> IDLE

The FSM ensures safe, controlled behavior:
- Food is only extended when the feeder is enabled
- The feeder retracts immediately upon detecting threats
- If bear gets very close, enters FEEDING state to let it eat safely
- Manual intervention required to retract from FEEDING state
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
        - Transition to FEEDING if bear gets very close (feeding distance)
        - Return to IDLE if disabled
    
    FEEDING:
        The arm is extended and bear is feeding.
        - Entered when bear reaches feeding distance in LURE state
        - Arm stays extended indefinitely for safe feeding
        - Only manual command can transition back to IDLE
        - Prevents bear from getting hurt grabbing food during retraction
    
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
    FEEDING = auto()
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
    - Distance-adaptive stillness thresholds (closer = stricter)
    - Time-based state transitions using deadlines
    
    Example Usage:
        feeder = FeederFSM(actuator, retract_delay_ms=500, cooldown_s=2.0)
        
        while True:
            threat_detected = motion_sensor.detect()
            feeder_enabled = user_control.is_enabled()
            distance = radar.get_distance()
            feeder.tick(enable=feeder_enabled, threat=threat_detected, radar_distance_m=distance)
            time.sleep(0.1)  # Call tick ~10 times per second
    """
    
    def __init__(self, actuator, retract_delay_ms: int, cooldown_s: float = 2.0, motion_threshold: float = 20.0, feeding_distance_m: float = 0.5, detection_distance_m: float = 3.0):
        """
        Initialize the feeder FSM.
        
        Args:
            actuator: An Actuator object with extend() and retract() methods
            retract_delay_ms: Delay in milliseconds between extending the lure and retracting it.
                            This allows the animal time to grab the food. Range: 0-3000ms typical.
            cooldown_s: Delay in seconds before allowing another extend cycle after retraction.
                       Prevents rapid cycling that could damage the mechanism. Default: 2.0s
            motion_threshold: Base movement magnitude threshold (in pixel units) at maximum distance.
                             This gets scaled down as bear gets closer for stricter stillness requirements.
                             Default: 20.0 pixels at 3m detection distance
            feeding_distance_m: Distance in meters at which bear is considered "close enough to feed".
                               When bear reaches this distance in LURE state, transitions to FEEDING.
                               Default: 0.5m
            detection_distance_m: Maximum distance in meters for threat detection.
                                Bear must be within this distance for the "game" to be active.
                                Motion thresholds scale linearly from this distance to feeding_distance.
                                Default: 3.0m
        """
        self.actuator = actuator
        # Convert milliseconds to seconds and ensure non-negative
        self.retract_delay_s = max(0.0, retract_delay_ms / 1000.0)
        self.cooldown_s = max(0.0, cooldown_s)
        self.base_motion_threshold = max(0.0, motion_threshold)  # Base threshold at max distance
        self.feeding_distance_m = max(0.0, feeding_distance_m)
        self.detection_distance_m = max(0.0, detection_distance_m)

        # Current state of the FSM
        self.state = State.IDLE
        # Deadline for transitioning to the next state (when now >= deadline, transition occurs)
        self._deadline = None
        # Flag to track if we've extended the lure in this IDLE period
        # Prevents multiple extends during a single enable period
        self._lure_extended_once = False

    def _adaptive_motion_threshold(self, radar_distance_m: float | None) -> float:
        """
        Calculate distance-adaptive motion threshold.
        
        As bear gets closer, stricter stillness is required (lower threshold).
        This mimics biological hunting behavior where close prey must be very still.
        
        Args:
            radar_distance_m: Current radar distance in meters, or None if unknown
            
        Returns:
            Motion threshold in pixels - lower values = stricter stillness required
            
        Scaling Logic:
        - At detection_distance_m (3m): use base_motion_threshold (most lenient)
        - At feeding_distance_m (0.5m): use minimum threshold (most strict)
        - Linear interpolation between these points
        - Beyond detection_distance_m: no threat detection (threshold = infinity)
        """
        if radar_distance_m is None:
            # No distance info available - use base threshold
            return self.base_motion_threshold
            
        if radar_distance_m > self.detection_distance_m:
            # Bear too far away - game hasn't started, no motion threats
            return float('inf')
            
        if radar_distance_m <= self.feeding_distance_m:
            # Bear very close - maximum stillness required
            return self.base_motion_threshold * 0.2  # 20% of base threshold
            
        # Linear interpolation between detection_distance and feeding_distance
        # Closer = stricter (lower threshold)
        distance_range = self.detection_distance_m - self.feeding_distance_m
        threshold_range = self.base_motion_threshold - (self.base_motion_threshold * 0.2)
        
        # How far along the distance range are we? (0.0 = far, 1.0 = close)
        progress = (self.detection_distance_m - radar_distance_m) / distance_range
        
        # Interpolate threshold: higher progress = lower threshold (stricter)
        adaptive_threshold = self.base_motion_threshold - (progress * threshold_range)
        
        return max(adaptive_threshold, self.base_motion_threshold * 0.2)  # Never go below minimum
        self.state = s
        self._deadline = deadline

    def tick(self, enable: bool, threat: bool, motion_magnitude: float | None = None, radar_distance_m: float | None = None, now: float | None = None):
        """
        Process one cycle of the state machine.
        
        This method should be called regularly (e.g., every 100ms) with the current
        enable and threat status. It handles all state transitions and actuator commands.
        
        Args:
            enable: Boolean indicating if the feeder is enabled (user control)
            threat: Boolean indicating if a threat is detected (motion sensor)
            motion_magnitude: Optional motion magnitude (e.g., from vision deltas), used as a supplementary threat indicator
            radar_distance_m: Optional current radar distance in meters, used to detect feeding proximity
            now: Optional timestamp from time.monotonic(). If None, uses current time.
                 Useful for testing with simulated time.
        
        Control Flow:
        1. If disabled: Immediately retract and go to IDLE (safety first)
        2. If enabled:
           - IDLE: Extend the lure and transition to LURE
           - LURE: Wait for threat detection (radar or vision) OR feeding proximity
           - FEEDING: Arm extended indefinitely for safe feeding, manual retract only
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
            # Check if bear is close enough to feed (bear "wins" scenario)
            if radar_distance_m is not None and radar_distance_m <= self.feeding_distance_m:
                # Bear is close enough! Let it feed safely
                self._set_state(State.FEEDING, deadline=None)  # Stay extended indefinitely
                return

            # Detection distance check: Only allow threat signals when bear is within detection_distance_m
            # If radar_distance_m > detection_distance_m, the game hasn't started yet, ignore threats
            bear_is_in_game = radar_distance_m is None or radar_distance_m <= self.detection_distance_m

            # Use adaptive motion threshold based on current distance
            current_motion_threshold = self._adaptive_motion_threshold(radar_distance_m)
            
            fused_threat = threat and bear_is_in_game  # Radar threat (sudden distance jump)
            if motion_magnitude is not None and motion_magnitude >= current_motion_threshold and bear_is_in_game:
                fused_threat = True  # Vision threat (motion exceeds adaptive threshold)

            if fused_threat:
                # Threat detected (radar or vision motion). Start the retract delay.
                # The retract_delay gives the animal time to grab the food
                self._set_state(State.RETRACT_WAIT, deadline=now + self.retract_delay_s)
            return

        # FEEDING state: Arm extended indefinitely for safe feeding
        if self.state == State.FEEDING:
            # Stay in FEEDING state until manual intervention
            # Only external command (like button press) can transition out
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

    def manual_retract(self, now: float | None = None) -> bool:
        """
        Manually retract the feeder arm from FEEDING state.
        
        This method allows external intervention (like a button press) to safely
        retract the arm when the bear has finished feeding.
        
        Args:
            now: Optional timestamp from time.monotonic(). If None, uses current time.
        
        Returns:
            True if retraction was initiated (was in FEEDING state), False otherwise
        """
        if self.state != State.FEEDING:
            return False
        
        now = now if now is not None else time.monotonic()
        self.actuator.retract()  # Immediately retract
        self._lure_extended_once = False  # Reset for next cycle
        self._set_state(State.IDLE, deadline=None)
        return True

   
