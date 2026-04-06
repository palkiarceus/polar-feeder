"""
RF Signal Transmission Module for Polar Feeder

This module handles transmitting pre-recorded RF (radio frequency) signals to control
the feeder actuators via remote control-like mechanism. The signals are stored as
JSON files containing recorded pulse patterns (states and durations).

The module provides three main functions:
- transmit1(): Send the "extend" command signal
- transmit2(): Send the "retract" command signal
- transmitwithdelay(delay_s): Send extend, wait, then send retract (full dispense cycle)

RF Signal Format:
- JSON file with two arrays: "states" (0s and 1s) and "durations" (times in seconds)
- Each pair represents a pulse: (state, duration) where state is GPIO level (0 or 1)
- Replays these pulses on GPIO17 to transmit the RF signal

Example JSON:
{
    "states": [0, 1, 0, 1, ...],
    "durations": [0.001, 0.002, 0.001, ...]
}
"""

import lgpio
import time
import json
from pathlib import Path

# ============================================================================
# Signal File Loading
# ============================================================================

# Prefer repo-root config/rf; fallback to package config for older layout
PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[3]  # Navigate up: .../src/pi/polar_feeder -> repo root
RF_DIR_CANDIDATES = [
    REPO_ROOT / "config" / "rf",  # New layout: config/rf/ directory
    PKG_DIR / "config",            # Fallback: legacy package-local config
]


def _load(filename: str):
    """
    Load a pre-recorded RF signal from a JSON file.
    
    Searches multiple directories for the signal file and loads the pulse data.
    The JSON should contain:
    - "states": array of 0s and 1s (GPIO levels)
    - "durations": array of floats (time in seconds for each state)
    
    Args:
        filename: Name of the JSON file (e.g., "rf_signal1.json")
        
    Returns:
        Tuple of (states list, durations list, path to loaded file)
        
    Raises:
        FileNotFoundError: If the signal file is not found in any candidate directory
        
    Example:
        states, durations, path = _load("rf_signal1.json")
        print(f"Loaded {len(states)} pulses from {path}")
    """
    # Try each candidate directory in order
    for d in RF_DIR_CANDIDATES:
        p = d / filename
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data["states"], data["durations"], p
    raise FileNotFoundError(f"RF signal file not found: {filename} (searched {RF_DIR_CANDIDATES})")


def _transmit(filename: str, tx_pin: int = 17) -> None:
    """
    Low-level function to transmit an RF signal by replaying pulse patterns on GPIO.
    
    This function:
    1. Loads the pulse data from a JSON file
    2. Opens the GPIO chip
    3. Claims the GPIO pin as output
    4. Replays each pulse by setting GPIO level and sleeping for the specified duration
    5. Ends with GPIO low (0)
    6. Closes the GPIO chip
    
    Args:
        filename: Name of the RF signal JSON file (e.g., "rf_signal1.json")
        tx_pin: GPIO pin number to transmit on. Default: 17 (common choice on RPi)
               This is the BCM (Broadcom) pin number, not the physical pin number.
        
    Raises:
        FileNotFoundError: If signal file is not found
        lgpio error: If GPIO operations fail (no permissions, invalid pin, etc.)
        
    Example:
        _transmit("rf_signal1.json", tx_pin=17)
    """
    # Load the pulse data
    states, durations, p = _load(filename)
    print(f"Loaded signal from {p} ({len(states)} pulses).", flush=True)

    # Initialize GPIO
    h = lgpio.gpiochip_open(0)
    try:
        # Configure the pin as output
        lgpio.gpio_claim_output(h, tx_pin)

        print(f"Transmitting {filename} on GPIO{tx_pin}...", flush=True)
        
        # Replay the pulse sequence
        for s, dt in zip(states, durations):
            lgpio.gpio_write(h, tx_pin, int(s))  # Set GPIO to state (0 or 1)
            time.sleep(float(dt))                 # Wait for duration

        # End with GPIO low (safety)
        lgpio.gpio_write(h, tx_pin, 0)
        print("Transmission complete.", flush=True)
    finally:
        # Always close GPIO, even if there's an error
        lgpio.gpiochip_close(h)


# ============================================================================
# Public API Functions
# ============================================================================

def transmit1():
    """
    Transmit the RF signal to EXTEND the feeder arm.
    
    Loads and replays rf_signal1.json, which contains the pulse pattern for the
    "extend" command. This causes the feeder arm to move outward/downward.
    
    Uses GPIO17 by default on the Raspberry Pi.
    
    Raises:
        FileNotFoundError: If rf_signal1.json is not found
        lgpio error: If GPIO operations fail
        
    Thread Safe: Yes - each call uses its own GPIO context
    """
    _transmit("rf_signal1.json", tx_pin=17)


def transmit2():
    """
    Transmit the RF signal to RETRACT the feeder arm.
    
    Loads and replays rf_signal2.json, which contains the pulse pattern for the
    "retract" command. This causes the feeder arm to move inward/upward to resting position.
    
    Uses GPIO17 by default on the Raspberry Pi.
    
    Raises:
        FileNotFoundError: If rf_signal2.json is not found
        lgpio error: If GPIO operations fail
        
    Thread Safe: Yes - each call uses its own GPIO context
    """
    _transmit("rf_signal2.json", tx_pin=17)


def transmitwithdelay(delay_s: float):
    """
    Perform a complete dispense cycle: extend, wait, then retract.
    
    This is the high-level function used by the Actuator class for actual food dispensing:
    1. Transmit RF_signal1 (extend)
    2. Sleep for delay_s seconds
    3. Transmit RF_signal2 (retract)
    
    The delay allows the animal time to grab the food before it's pulled away.
    
    Args:
        delay_s: Delay in seconds between extend and retract. Should be in range 0-3 seconds.
                Typical values: 0.5 - 2.5 seconds depending on food dispensing amount desired.
                
    Raises:
        FileNotFoundError: If signal files are not found
        lgpio error: If GPIO operations fail
        
    Example:
        transmitwithdelay(1.0)  # Extend for 1 second, then retract
        
    Thread Safe: Yes - each call uses its own GPIO context
    """
    transmit1()                      # Extend the arm
    time.sleep(float(delay_s))      # Wait for specified delay
    transmit2()                      # Retract the arm
