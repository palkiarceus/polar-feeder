"""
RF Signal Recording Utility for Polar Feeder

This script records RF (radio frequency) signals from a remote control or RF receiver
and saves them as JSON pulse patterns. These recorded patterns can then be replayed
via transmittingfunc.py to control the feeder actuators.

Typical Workflow:
1. Run this script: python receivingsave.py
2. Press a button on the remote control
3. Script records the signal for 3 seconds
4. Results are saved to a JSON file (e.g., rf_signal2.json)
5. Use the recorded signal with transmit2() or similar

The recorded signal contains:
- states: Array of GPIO levels (0 = low, 1 = high) representing the RF signal
- durations: Array of times (in seconds) that each state lasts

This creates a complete pulse train that, when replayed on GPIO, recreates the
original RF transmission.
"""

import lgpio
import time
import json

# ============================================================================
# Configuration
# ============================================================================

# GPIO pin connected to the RF receiver
# This should be connected to the receiver's data output
RX_PIN = 27

# ============================================================================
# Main Recording Logic
# ============================================================================

# Open GPIO chip 0 (the main GPIO interface on Raspberry Pi)
h = lgpio.gpiochip_open(0)
# Configure RX_PIN as input to receive RF signals
lgpio.gpio_claim_input(h, RX_PIN)

print("Waiting for RF signal... Press remote button.")

# Lists to store the recorded signal
pulse_lengths = []  # Time duration of each pulse
states = []         # GPIO level (0 or 1) of each state

# Wait for first edge (button press)
# Poll the GPIO until we see a state change
last_state = lgpio.gpio_read(h, RX_PIN)
while True:
    state = lgpio.gpio_read(h, RX_PIN)
    if state != last_state:
        break  # State changed - signal detected!
    time.sleep(0.00001)  # Sleep briefly to avoid busy-waiting

print("Signal detected! Recording for 3 seconds...")

# Record the signal for 3 seconds
start_time = time.time()
last_time = start_time
last_state = lgpio.gpio_read(h, RX_PIN)

while time.time() - start_time < 3:  # Record for 3 seconds
    state = lgpio.gpio_read(h, RX_PIN)
    now = time.time()

    if state != last_state:
        # State change detected - record the duration
        duration = now - last_time
        pulse_lengths.append(duration)
        states.append(last_state)  # Store the STATE that just ended

        last_state = state
        last_time = now

    time.sleep(0.000005)  # Sleep 5 microseconds - very responsive

# Clean up GPIO
lgpio.gpiochip_close(h)

print("Recording complete.")
print("Captured pulses:", len(pulse_lengths))

# ============================================================================
# Save to File
# ============================================================================

# Create the data structure in the format expected by transmittingfunc.py
data = {
    "states": states,
    "durations": pulse_lengths
}

# Write to JSON file
with open("rf_signal2.json", "w") as f:
    json.dump(data, f)

print("Signal saved to rf_signal2.json")