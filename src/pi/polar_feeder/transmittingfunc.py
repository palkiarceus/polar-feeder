import lgpio
import time
import json
from pathlib import Path

CFG_DIR = Path(__file__).resolve().parent / "config"

def _load(filename:str):
    p = CFG_DIR / filename
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data["states"], data["durations"], p
    
def transmit1():
    TX_PIN = 17
    states, durations, p = _load("rf_signal1.json")
    print(f"Loaded signal from {p} ({len(states)} pulses).", flush=True)

    h = lgpio.gpiochip_open(0)
    lgpio.gpio_claim_output(h, TX_PIN)

    print("Transmitting 1 now...", flush=True)
    for s, dt in zip(states, durations):
        lgpio.gpio_write(h, TX_PIN, s)
        time.sleep(dt)

    lgpio.gpio_write(h, TX_PIN, 0)
    lgpio.gpiochip_close(h)
    print("Transmission complete.", flush=True)
    # Transmit signal
    for i in range(len(states)):
        lgpio.gpio_write(h, TX_PIN, states[i])
        time.sleep(durations[i])

    # Ensure line ends LOW
    lgpio.gpio_write(h, TX_PIN, 0)

    lgpio.gpiochip_close(h)

    print("Transmission complete.")
    return

def transmit2():
    TX_PIN = 17  # change to 27 if retract should transmit on GPIO27
    states, durations, p = _load("rf_signal2.json")
    print(f"Loaded signal from {p} ({len(states)} pulses).", flush=True)

    h = lgpio.gpiochip_open(0)
    lgpio.gpio_claim_output(h, TX_PIN)

    print("Transmitting 2 now...", flush=True)
    for s, dt in zip(states, durations):
        lgpio.gpio_write(h, TX_PIN, s)
        time.sleep(dt)

    lgpio.gpio_write(h, TX_PIN, 0)
    lgpio.gpiochip_close(h)
    print("Transmission complete.", flush=True)

def transmitwithdelay(delay):
    transmit1()
    time.sleep(delay)
    transmit2()
    return


