# main_control.py
import threading
import time

from actuator_gpio import ActuatorGPIO
from feeder_fsm import FeederFSM
from ble_interface import BleServer, BleCommand  # your existing file

class SharedInputs:
    def __init__(self):
        self.lock = threading.Lock()
        self.enable = False
        self.last_cmd_monotonic = time.monotonic()

    def set_enable(self, v: bool):
        with self.lock:
            self.enable = v
            self.last_cmd_monotonic = time.monotonic()

    def snapshot(self):
        with self.lock:
            return self.enable, self.last_cmd_monotonic

def parse_ble_command(cmd: str):
    # Accept: ENABLE=1 / ENABLE=0
    s = cmd.strip().upper()
    if s.startswith("ENABLE="):
        val = s.split("=", 1)[1].strip()
        if val in ("1", "TRUE", "ON"):
            return ("enable", True)
        if val in ("0", "FALSE", "OFF"):
            return ("enable", False)
    return ("unknown", cmd)

def run():
    shared = SharedInputs()

    # Pick GPIO pins (BCM). Change these to whatever you wired.
    EXTEND_PIN = 17
    RETRACT_PIN = 27

    pulse_ms = 200          # from your JSON later
    retract_delay_ms = 2500 # from your JSON later
    cooldown_s = 2.0

    actuator = ActuatorGPIO(EXTEND_PIN, RETRACT_PIN, pulse_ms=pulse_ms)
    fsm = FeederFSM(actuator, retract_delay_ms=retract_delay_ms, cooldown_s=cooldown_s)

    ble = BleServer(name="PolarFeeder")

    def on_cmd(c: BleCommand) -> str:
        kind, val = parse_ble_command(c.raw)
        if kind == "enable":
            shared.set_enable(val)
            return f"ACK ENABLE={1 if val else 0}"
        return "ERR UNKNOWN_CMD"

    ble.set_command_handler(on_cmd)

    # BLE runs blocking, so run it in a thread
    t = threading.Thread(target=ble.start, daemon=True)
    t.start()

    tick_hz = 20.0
    tick_dt = 1.0 / tick_hz

    while True:
        enable, last_cmd = shared.snapshot()

        # Threat stub for now — keep False for integration
        threat = False

        # Optional: if BLE goes silent for too long, safe-idle
        # (you already have a config flag for this concept)
        if time.monotonic() - last_cmd > 60.0:
            enable = False

        fsm.tick(enable=enable, threat=threat)
        time.sleep(tick_dt)

if __name__ == "__main__":
    run()
