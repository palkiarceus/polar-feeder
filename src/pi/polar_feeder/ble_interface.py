"""
BLE GATT server (Pi as Peripheral) using bluezero + BlueZ.

Service: Nordic UART-style UUIDs
- RX characteristic (write): ASCII commands, e.g. ENABLE=1, ENABLE=0
- TX characteristic (notify): ACK/status
"""

from dataclasses import dataclass
from typing import Callable, Optional

from bluezero import peripheral

SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notify
RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # write


def _b(s: str) -> bytes:
    return s.encode("utf-8")


@dataclass
class BleCommand:
    raw: str


class BleServer:
    def __init__(self, name: str = "PolarFeeder"):
        self.name = name
        self._periph: Optional[peripheral.Peripheral] = None
        self._tx_char: Optional[peripheral.Characteristic] = None
        self._on_command: Optional[Callable[[BleCommand], None]] = None

    def set_command_handler(self, fn: Callable[[BleCommand], None]):
        self._on_command = fn

    def notify(self, text: str):
        if not self._tx_char:
            return
        self._tx_char.set_value(list(_b(text)))
        self._tx_char.notify()

    def _on_write(self, value: bytes, options):
        msg = value.decode("utf-8", errors="ignore").replace("\x00", "").strip()
        if msg and self._on_command:
            self._on_command(BleCommand(raw=msg))
        self.notify(f"ACK:{msg}" if msg else "ACK")

    def start(self):
        srv = peripheral.Service(SERVICE_UUID, primary=True)

        self._tx_char = peripheral.Characteristic(
            TX_CHAR_UUID,
            ["notify"],
            srv,
            value=list(_b("BOOT")),
        )

        rx_char = peripheral.Characteristic(
            RX_CHAR_UUID,
            ["write", "write-without-response"],
            srv,
            value=list(_b("")),
        )
        rx_char.add_write_event(self._on_write)

        self._periph = peripheral.Peripheral(self.name, [srv])
        self._periph.publish()  # blocks forever
