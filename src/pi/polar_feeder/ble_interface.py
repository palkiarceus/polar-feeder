from dataclasses import dataclass
from typing import Callable, Optional, List
import subprocess
import time

from bluezero import peripheral

SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notify/read
RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # write

def _str_to_bytes_list(s: str) -> List[int]:
    return list(s.encode("utf-8"))

def _bytes_list_to_str(v) -> str:
    try:
        msg = bytes(v).decode("utf-8", errors="ignore")
        return msg.replace("\x00", "")
    except Exception:
        return ""

def _get_adapter_addr() -> str:
    out = subprocess.check_output(["bluetoothctl", "list"], text=True, stderr=subprocess.STDOUT)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Controller "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    raise RuntimeError("No BLE adapter found (bluetoothctl list returned none).")

@dataclass
class BleCommand:
    raw: str

class BleServer:
    def __init__(self, name: str = "PolarFeeder", adapter_addr: Optional[str] = None):
        self.name = name
        self.adapter_addr = adapter_addr or _get_adapter_addr()

        self._p: Optional[peripheral.Peripheral] = None
        self._on_command: Optional[Callable[[BleCommand], str]] = None  # return response string

        self._srv_id = 1
        self._rx_id = 1
        self._tx_id = 2

        self._tx_value = "BOOT\n"
        self._rx_buf = ""  # newline framing buffer
        self.last_rx_time = time.time()

    def set_command_handler(self, fn: Callable[[BleCommand], str]):
        """
        Handler returns a response string like:
          "ACK ENABLE=1"
          "ERR OUT_OF_RANGE retract_delay_ms 0 3000"
        """
        self._on_command = fn

    def _read_tx(self):
        return _str_to_bytes_list(self._tx_value)

    def _on_write_rx(self, value, options):
        chunk = _bytes_list_to_str(value)
        self.last_rx_time = time.time()

        # Keep raw logging useful
        print("[BLE WRITE] chunk:", repr(chunk), "raw:", value, "options:", options, flush=True)

        if not chunk:
            self.notify("ERR EMPTY\n")
            return

        # Normalize common escape sequences some clients send literally
        # e.g. "ENABLE=1\\n" -> "ENABLE=1\n"
        chunk = chunk.replace("\\r", "\r").replace("\\n", "\n")

        self._rx_buf += chunk
        responses: List[str] = []

        def process_line(line: str) -> None:
            line = line.strip()
            if not line:
                return
            if self._on_command:
                try:
                    resp = self._on_command(BleCommand(raw=line))
                except Exception as e:
                    resp = f"ERR EXCEPTION {type(e).__name__}"
            else:
                resp = "ERR NO_HANDLER"
            responses.append(resp.strip())

        # 1) If we have real newlines, process all complete lines
        while "\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split("\n", 1)
            process_line(line)

        # 2) If there were no newlines at all, treat this write as a full command
        # This makes nRF Connect + MIT App Inventor behave nicely.
        if not responses and self._rx_buf.strip():
            process_line(self._rx_buf)
            self._rx_buf = ""

        if responses:
            self.notify("\n".join(responses) + "\n")


    def notify(self, text: str):
        """
        Best-effort notify across different bluezero versions.
        Always updates the readable TX value.
        """
        if not self._p:
            return

        self._tx_value = text

        # Try a few possible notify method names depending on bluezero version.
        for method_name in ("notify", "send_notify_event", "notify_characteristic"):
            m = getattr(self._p, method_name, None)
            if callable(m):
                try:
                    m(self._srv_id, self._tx_id)
                    return
                except Exception as e:
                    print(f"[BLE] notify via {method_name} failed: {e}", flush=True)

        # Fallback: no notify available; rely on TX being readable
        print("[BLE] No notify method available on this bluezero version; TX is readable (poll/read to get updates).", flush=True)


    def start(self):
        print("BLE Server Starting", flush=True)
        self._p = peripheral.Peripheral(adapter_address=self.adapter_addr, local_name=self.name)

        self._p.add_service(srv_id=self._srv_id, uuid=SERVICE_UUID, primary=True)

        self._p.add_characteristic(
            srv_id=self._srv_id,
            chr_id=self._rx_id,
            uuid=RX_CHAR_UUID,
            flags=["write", "write-without-response"],
            value=_str_to_bytes_list(""),
            notifying=False,
            write_callback=self._on_write_rx,
        )

        self._p.add_characteristic(
            srv_id=self._srv_id,
            chr_id=self._tx_id,
            uuid=TX_CHAR_UUID,
            flags=["notify", "read"],
            value=_str_to_bytes_list(self._tx_value),
            notifying=True,
            read_callback=self._read_tx,
        )

        print("BLE Publishing Now", flush=True)
        self._p.publish()
