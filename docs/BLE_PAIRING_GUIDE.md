# Polar Feeder — BLE Pairing Guide

## How the Bluetooth connection works

The Pi runs a BLE GATT server using the Nordic UART Service (NUS) profile.
The Android app connects as a BLE client and sends newline-terminated commands.

The following configuration makes pairing automatic and persistent:

| Setting | File | Value | Why |
|---|---|---|---|
| `JustWorksRepairing` | `/etc/bluetooth/main.conf` | `always` | Re-pairs silently after Pi reboot without prompting |
| `Privacy` | `/etc/bluetooth/main.conf` | `off` | Keeps MAC address stable across reboots so Android recognizes the Pi |
| `SecureConnections` | `/etc/bluetooth/main.conf` | `off` | Prevents Android OS error 5 (GATT_INSUF_AUTHENTICATION) timeout |
| `DiscoverableTimeout` | `/etc/bluetooth/main.conf` | `0` | Always discoverable, never times out |
| `PairableTimeout` | `/etc/bluetooth/main.conf` | `0` | Always pairable, never times out |
| Persistent agent | `ble_interface.py` | `NoInputNoOutput` | Pi auto-accepts pairing without any prompt or button press |
| RX characteristic | `ble_interface.py` | `write-without-response` | Avoids Android triggering authentication requirement before writes |
| Bond cache | service file | NOT cleared on restart | Bond survives Pi reboots so Android reconnects silently |

---

## Normal operation (after initial pairing)

1. Power on Pi — service starts automatically via systemd
2. Open Android app — connects silently, no prompt on either device
3. Send commands — work immediately

No Pi interaction ever required during normal operation.

---

## Initial pairing (first time setup)

Do this once on a fresh Pi or after a factory reset:

**Step 1 — Ensure both sides are clean:**
```bash
# On the Pi, check for any existing bonds
bluetoothctl
devices
# If the Android phone appears, remove it:
remove <PHONE_MAC_ADDRESS>
quit
```

Also forget the Pi on the Android side:
- Android Settings → Connected Devices → PolarFeeder → Forget

**Step 2 — Start the service:**
```bash
sudo systemctl restart polar-feeder
```

**Step 3 — Connect from the app:**
- Open the app and connect to PolarFeeder
- Accept the pairing prompt on the Android when it appears
- The Pi will auto-accept silently (no prompt on Pi side)

**Step 4 — Verify:**
- Send a command from the app (e.g., STATUS)
- Should receive `ACK STATUS ...` back

The bond is now stored permanently on both devices.

---

## Clean re-pair (if connection breaks permanently)

Use this if you get persistent OS error 133 (GATT_ERROR) or OS error 5
(GATT_INSUF_AUTHENTICATION) that won't resolve with a simple reconnect.

This happens when Android and the Pi have mismatched bond states — one side
remembers the bond and the other doesn't.

**Step 1 — Clear Android's BLE cache:**
- Android Settings → Apps → Show system apps → Bluetooth → Storage → Clear Cache
- Android Settings → Connected Devices → PolarFeeder → Forget device
- Toggle Bluetooth off and back on on the phone

**Step 2 — Clear the bond on the Pi:**
```bash
bluetoothctl
devices
# Find the Android phone MAC address in the list
remove <PHONE_MAC_ADDRESS>
quit
```

**Step 3 — Restart the service:**
```bash
sudo systemctl restart polar-feeder
```

**Step 4 — Re-pair:**
- Connect from the app
- Accept pairing prompt on Android
- Pi auto-accepts silently
- Test with a command

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| OS error 5 on Android | Bond mismatch or authentication timeout | Do clean re-pair procedure above |
| OS error 133 on Android | Stale GATT cache on Android | Clear Android BLE cache, do clean re-pair |
| App shows "connected" but commands don't register | Stale BLE session (old connection handle) | Toggle Bluetooth off/on on Android |
| Pairing prompt appears on Pi | Old unpaired session pending | Accept it, or restart service and re-pair |
| Service fails to start | Bluetooth adapter not ready | `sudo systemctl restart bluetooth` then `sudo systemctl restart polar-feeder` |

---

## Checking service status and logs

```bash
# Check if service is running
sudo systemctl status polar-feeder

# Watch live logs
journalctl -u polar-feeder -f

# See last 50 lines
journalctl -u polar-feeder -n 50 --no-pager

# Check stored bonds on Pi
bluetoothctl devices
```

---

## Files involved in BLE configuration

```
polar-feeder/
├── polar-feeder.service          # Systemd service (copy to /etc/systemd/system/)
├── bluetooth/
│   └── main.conf                 # BlueZ config (copy to /etc/bluetooth/main.conf)
└── src/pi/polar_feeder/
    ├── ble_interface.py          # BLE GATT server implementation
    └── main.py                   # BLE command handler
```

**To deploy config files to system:**
```bash
sudo cp polar-feeder.service /etc/systemd/system/polar-feeder.service
sudo cp bluetooth/main.conf /etc/bluetooth/main.conf
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo systemctl restart polar-feeder
```