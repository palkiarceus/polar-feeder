# Hardware Adaptation Guide

## Purpose

This guide explains how to adapt Polar Feeder to new hardware, including:
- Replacing or extending the actuator hardware
- Adding a new distance or motion sensor
- Keeping the existing software architecture and FSM behavior
- Documenting new hardware for future developers

## Key abstraction points

The current code is intentionally split into layers:

- `src/pi/polar_feeder/main.py` — orchestration, mode selection, BLE command handling, session logging
- `src/pi/polar_feeder/feeder_fsm.py` — core state machine and safety logic
- `src/pi/polar_feeder/actuator.py` — actuator command API (`extend`, `retract`, `extend_then_retract`)
- `src/pi/polar_feeder/transmittingfunc.py` — RF transmit implementation
- `src/pi/polar_feeder/radar.py` — sensor reader and threat detection
- `src/pi/polar_feeder/ble_interface.py` — BLE command/response transport
- `config/config.example.json` — runtime hardware and behavior configuration

That means most new hardware work should be done by replacing or extending the hardware-specific modules while leaving `main.py`, `feeder_fsm.py`, and `ble_interface.py` unchanged.

## How the software works with hardware

### What the system expects from hardware

The system is built around three main hardware roles:

1. **Actuator output** — a mechanism that can extend and retract the feeding arm
2. **Threat/distance sensor input** — a sensor that tells the software when a threat is present
3. **Optional vision/camera input** — additional movement detection that can augment radar

The control loop and FSM are hardware-agnostic:
- `ENABLE=1` starts the feeding process
- `ENABLE=0` forces safe idle and retraction
- `actuator.extend()` and `actuator.retract()` are the only direct hardware outputs the FSM uses
- `threat` is a boolean input that can come from radar, vision, or a combined sensor fusion layer

### What should remain unchanged

Keep the existing logic in:
- `feeder_fsm.py` — state transitions and timings
- `ble_interface.py` — BLE transport and command handling
- `config/` — configuration-driven behavior

### What can change safely

Hardware-specific implementations may change in:
- `actuator.py` / `transmittingfunc.py`
- `radar.py`
- `src/pi/polar_feeder/vision.py`
- `config/schema.json` and `config/config.example.json`

## Adapting actuator hardware

### Existing actuator pattern

`src/pi/polar_feeder/actuator.py` defines:
- `Actuator.open()`
- `Actuator.close()`
- `Actuator.extend()`
- `Actuator.retract()`
- `Actuator.extend_then_retract(delay_s)`

The FSM only depends on these methods, so new actuator hardware should implement the same API.

### Replace RF control with a new actuator

If your new hardware uses a relay, servo, or motor controller instead of RF:

1. Create a new actuator class in `src/pi/polar_feeder/actuator.py` or a new module.
2. Keep the same public methods.
3. In `main.py`, instantiate the new actuator instead of the existing `Actuator`.
4. Use config values for pin numbers, pulse widths, or GPIO device names.

Example:

```python
class RelayActuator:
    def __init__(self, gpio_chip=0, extend_pin=17, retract_pin=22):
        self.gpio_chip = gpio_chip
        self.extend_pin = extend_pin
        self.retract_pin = retract_pin

    def open(self):
        # claim pins and initialize outputs
        pass

    def close(self):
        # release pins
        pass

    def extend(self):
        # energize relay for extend
        pass

    def retract(self):
        # energize relay for retract
        pass
```

### Maintaining safety

- Always ensure `retract()` is safe to call any time
- Keep safe idle in `ENABLE=0`
- Keep any new hardware testable without the full system

## Adding a new threat/distance sensor

### Existing radar pattern

`src/pi/polar_feeder/radar.py` exposes the `RadarReader` class and `RadarReading` data type. It reads a serial sensor, parses data, and computes:
- `distance_m`
- `threat`
- `bin_index`

A new sensor can be added using the same pattern:
- build a reader class
- provide a `get_latest()` method
- return a data object with `distance_m` and `threat`

### New sensor example

If the new hardware is an ultrasonic distance sensor, the reader should:
1. read raw values from the hardware interface
2. compute a filtered distance
3. decide if the change in distance or object proximity constitutes a threat
4. return a structure the main loop can use

Example:

```python
class UltrasonicReader:
    def __init__(self, trigger_pin, echo_pin, max_distance_m):
        self.trigger_pin = trigger_pin
        self.echo_pin = echo_pin
        self.max_distance_m = max_distance_m

    def start(self):
        # start background thread or scheduler
        pass

    def get_latest(self):
        return RadarReading(
            bin_index=0,
            distance_m=measured_distance,
            threat=measured_distance < threat_distance,
            valid=True,
        )
```

### Sensor fusion

`src/pi/polar_feeder/vision.py` already demonstrates how vision data can be combined with radar. If your new hardware has multiple input sources, use a single fusion layer to publish a single threat signal to the FSM.

## Using the code with a new hardware platform

### Step-by-step adaptation process

1. **Review the existing interfaces**
   - `Actuator` methods in `actuator.py`
   - `RadarReader` in `radar.py`
   - BLE command handler in `ble_interface.py`

2. **Add or replace a hardware module**
   - Add the new hardware code in `src/pi/polar_feeder/`
   - Keep the external API compatible with existing callers

3. **Update configuration**
   - Add new config keys to `config/config.example.json`
   - Add schema entries in `config/schema.json`
   - Document new keys in `docs/CONFIG_GUIDE.md`

4. **Wire the hardware safely**
   - Test hardware interaction separately before full system integration
   - Keep the actuator in a safe state between tests

5. **Test without hardware first**
   - Use `python src/pi/polar_feeder/main.py --demo-seconds 30`
   - Add simulation stubs for the new hardware if needed

6. **Integrate into BLE mode**
   - If the new hardware influences commands, update `main.py` command handling
   - Prefer not to change BLE commands unless absolutely necessary

7. **Document the new hardware**
   - Add a section to `docs/HARDWARE_ADAPTATION.md`
   - Add a short note to `docs/DOCUMENTATION_INDEX.md`
   - Add any new setup instructions to `README.md`

## Example adaptation scenarios

### Example 1: Direct GPIO relay actuator

- Replace RF pulse replay with a relay-actuated motor.
- Use `lgpio` to claim relay pins.
- Build a new `RelayActuator` that maps `extend()` and `retract()` to relay states.
- Keep the FSM and BLE layer unchanged.

### Example 2: New serial distance sensor

- Replace `RadarReader` with a new serial reader using the same API.
- Use the same `RadarReading` fields to preserve downstream code.
- Use config keys for port, baud, and sensitivity.

### Example 3: New actuator with feedback

- If the new actuator provides position or limit-switch feedback, build a feedback wrapper:
  - extend until position reached
  - retract until limit switch triggered
  - raise an exception or log if the actuator fails
- Keep the FSM interface intact.

## Important implementation notes

- `src/pi/polar_feeder/main.py` currently hard-codes `GPIO17` for RF output in `transmittingfunc.py`.
  - If adapting to new hardware, consider making the GPIO pin configurable.
- `config/config.example.json` is the authoritative user-facing config.
  - Keep `src/pi/polar_feeder/config/config.example.json` in sync if you add new config fields.
- `config/schema.json` is used for validation.
  - Add new keys there to prevent invalid deployments.

## Documenting new hardware support

When adding support for new hardware, add the following documentation:
- hardware wiring diagram or pin assignment
- supported commands and behavior changes
- configuration keys and example values
- safety and fail-safe behavior
- testing procedure

Use this file as the central place for adaptation guidance and link to it from `README.md` and `docs/DOCUMENTATION_INDEX.md`.
