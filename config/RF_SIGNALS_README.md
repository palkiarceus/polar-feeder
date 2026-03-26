# RF Signal Files

This directory contains pre-recorded RF (radio frequency) signal patterns used to control the feeder actuators.

## Files

### rf_signal1.json
**Purpose:** EXTEND command - tells the feeder to extend the arm outward

**Format:**
```json
{
  "states": [0, 1, 0, 1, ...],     // GPIO levels (0=low, 1=high)
  "durations": [0.001, 0.002, ...]  // Time in seconds for each state
}
```

**Usage:** Called by `transmit1()` in transmittingfunc.py

**How it works:**
- Recorded from an RF remote control button press
- Each (state, duration) pair represents a pulse sent on GPIO17
- When replayed, recreates the original RF transmission pattern
- The receiver on the feeder arm receives this signal and actuates

### rf_signal2.json
**Purpose:** RETRACT command - tells the feeder to retract the arm inward

**Format:** Same as rf_signal1.json

**Usage:** Called by `transmit2()` in transmittingfunc.py

## Creating New Signals

To record a new RF signal (e.g., from a different remote):

1. Run the recording script:
   ```bash
   python receivingsave.py
   ```

2. Press the desired button on the remote control

3. The script records pulses for 3 seconds and saves to `rf_signal2.json`

4. Move the saved file to a new filename if needed (e.g., `rf_signal_custom.json`)

5. Update transmittingfunc.py to use the new signal file

## Technical Details

- **GPIO Pin:** 17 (BCM numbering on Raspberry Pi)
- **Recording Pin:** 27 (BCM numbering - connects to RF receiver data output)
- **Format:** JSON with pulse state/duration arrays
- **Timing:** Precision down to microseconds for accurate RF reproduction
- **Replay Method:** Bit-banged GPIO (software-timed) at 5μs resolution

## Safety Notes

- Signals are specific to the RF receiver chip used
- Different remote controls use different protocols
- If signals don't work with your feeder, record new ones using `receivingsave.py`
- Always test signals slowly with the feeder disabled until confident they work
