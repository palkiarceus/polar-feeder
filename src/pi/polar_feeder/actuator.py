"""
Actuator Module for Polar Feeder

This module provides an interface to control the physical actuators of the Polar Feeder device.
The actuators are RF-controlled relays that trigger mechanical actions (extend/retract).

The Actuator class abstracts away the low-level RF transmission details and provides a simple
API for controlling feeder mechanics. It supports:
- Extending the feeder arm (dispenses food)
- Retracting the feeder arm (stores it)
- Extending then retracting with a configurable delay between actions

All RF transmissions are performed via the transmitting module which handles the actual
radio frequency signal generation.
"""

from dataclasses import dataclass
from polar_feeder.transmittingfunc import transmit1, transmit2, transmitwithdelay


@dataclass
class Actuator:
    """
    Control interface for the RF-controlled actuator system.
    
    This class manages the mechanical arm/hopper of the Polar Feeder. It sends RF (radio frequency)
    signals to trigger relays that control solenoids or motors for extending/retracting.
    
    Attributes:
        retract_delay_s: Default delay (in seconds) between extend and retract actions.
                        Used by extend_then_retract() if no explicit delay is provided.
                        Default is 0.0 (immediate retraction).
    
    Implementation Notes:
    - Uses the lgpio (Linux GPIO) approach: no persistent state needed
    - All actual RF transmission is delegated to the transmittingfunc module
    - The class is stateless except for the retract_delay_s configuration
    """
    retract_delay_s: float = 0.0

    def open(self) -> None:
        """
        Open/initialize the actuator connection.
        
        In the current lgpio implementation, this is a no-op since there's no persistent
        connection or resources to manage. GPIO handles everything at the OS level.
        
        This method exists for API consistency and future compatibility.
        """
        # nothing persistent needed for lgpio approach
        return

    def close(self) -> None:
        """
        Close/cleanup the actuator connection.
        
        In the current lgpio implementation, this is a no-op. No cleanup is needed since
        the GPIO resources are managed by the Linux kernel.
        
        This method exists for API consistency and future compatibility.
        """
        return

    def extend(self, duration_s=None) -> None:
        """
        Extend the feeder arm (dispense food).
        
        Sends an RF signal (transmit1) to trigger the extend relay, which causes the
        mechanical arm to move outward/downward to dispense food.
        
        Args:
            duration_s: Optional duration parameter (currently unused).
                       Included for API consistency in case future implementations need it.
        """
        transmit1()

    def retract(self, duration_s=None) -> None:
        """
        Retract the feeder arm (store/reset).
        
        Sends an RF signal (transmit2) to trigger the retract relay, which causes the
        mechanical arm to move inward/upward to its resting position.
        
        Args:
            duration_s: Optional duration parameter (currently unused).
                       Included for API consistency in case future implementations need it.
        """
        transmit2()

    def extend_then_retract(self, delay_s: float | None = None) -> None:
        """
        Extend the arm, wait, then retract it (complete dispense cycle).
        
        This is the primary method for dispensing food. It:
        1. Extends the arm (transmit1)
        2. Waits for the specified delay
        3. Retracts the arm (transmit2)
        
        The delay between extend and retract controls how much food is dispensed.
        
        Args:
            delay_s: Delay in seconds between extend and retract.
                    If None, uses the instance's retract_delay_s default.
                    This allows per-call configuration while maintaining a sensible default.
        """
        d = self.retract_delay_s if delay_s is None else float(delay_s)
        transmitwithdelay(d)
