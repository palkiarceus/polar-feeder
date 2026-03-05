import lgpio
import time
import json
from pathlib import Path

# Prefer repo-root config/rf; fallback to package config for older layout
PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[3]  # .../src/pi/polar_feeder -> repo root
RF_DIR_CANDIDATES = [
    REPO_ROOT / "config" / "rf",
    PKG_DIR / "config",
]

def _load(filename: str):
    for d in RF_DIR_CANDIDATES:
        p = d / filename
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data["states"], data["durations"], p
    raise FileNotFoundError(f"RF signal file not found: {filename} (searched {RF_DIR_CANDIDATES})")

def _transmit(filename: str, tx_pin: int = 17) -> None:
    states, durations, p = _load(filename)
    print(f"Loaded signal from {p} ({len(states)} pulses).", flush=True)

    h = lgpio.gpiochip_open(0)
    try:
        lgpio.gpio_claim_output(h, tx_pin)

        print(f"Transmitting {filename} on GPIO{tx_pin}...", flush=True)
        for s, dt in zip(states, durations):
            lgpio.gpio_write(h, tx_pin, int(s))
            time.sleep(float(dt))

        lgpio.gpio_write(h, tx_pin, 0)
        print("Transmission complete.", flush=True)
    finally:
        lgpio.gpiochip_close(h)

def transmit1():
    _transmit("rf_signal1.json", tx_pin=17)

def transmit2():
    _transmit("rf_signal2.json", tx_pin=17)

def transmitwithdelay(delay_s: float):
    transmit1()
    time.sleep(float(delay_s))
    transmit2()
