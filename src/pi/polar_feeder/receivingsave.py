import lgpio
import time
import json

RX_PIN = 27

h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_input(h, RX_PIN)

print("Waiting for RF signal... Press remote button.")

pulse_lengths = []
states = []

# Wait for first edge (button press)
last_state = lgpio.gpio_read(h, RX_PIN)
while True:
    state = lgpio.gpio_read(h, RX_PIN)
    if state != last_state:
        break
    time.sleep(0.00001)

print("Signal detected! Recording for 3 seconds...")

start_time = time.time()
last_time = start_time
last_state = lgpio.gpio_read(h, RX_PIN)

# Record for 3 seconds
while time.time() - start_time < 3:
    state = lgpio.gpio_read(h, RX_PIN)
    now = time.time()

    if state != last_state:
        duration = now - last_time
        pulse_lengths.append(duration)
        states.append(last_state)

        last_state = state
        last_time = now

    time.sleep(0.000005)

lgpio.gpiochip_close(h)

print("Recording complete.")
print("Captured pulses:", len(pulse_lengths))

# Save to file
data = {
    "states": states,
    "durations": pulse_lengths
}

with open("rf_signal2.json", "w") as f:
    json.dump(data, f)

print("Signal saved to rf_signal2.json")