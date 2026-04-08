"""
BLE Interface Module for Polar Feeder

This module implements a BLE (Bluetooth Low Energy) server that allows remote communication
with the Polar Feeder device. It provides a command-response interface over BLE using the
Nordic UART Service (NUS) profile, which is a common standard for BLE serial communication.

Key Components:
- BleServer: Main class that manages BLE advertising and communication
- BleCommand: Data class for encapsulating incoming BLE commands
- Helper functions: Utilities for converting between strings and BLE-compatible byte lists

The server listens for commands on the RX characteristic and sends responses via the TX
characteristic using a newline-delimited protocol.
"""

from dataclasses import dataclass
from typing import Callable, Optional, List
import subprocess
import time

from bluezero import peripheral

# BLE UUIDs for the Nordic UART Service (NUS) - a standard profile for serial-like communication
SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"  # Primary service UUID
TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # TX characteristic (server -> client): notify/read
RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # RX characteristic (client -> server): write

# ============================================================================
# Helper Functions for Data Conversion
# ============================================================================

def _str_to_bytes_list(s: str) -> List[int]:
    """
    Convert a string to a list of integers (byte values) for BLE transmission.
    
    Args:
        s: String to convert (e.g., "ENABLE=1\\n")
        
    Returns:
        List of integers representing the UTF-8 encoded bytes of the string
        
    Example:
        _str_to_bytes_list("Hi") returns [72, 105]
    """
    return list(s.encode("utf-8"))

def _bytes_list_to_str(v) -> str:
    """
    Convert a list of byte values back into a string, handling encoding errors gracefully.
    
    This function:
    - Attempts to decode the bytes as UTF-8
    - Ignores any decoding errors (treats invalid bytes as missing)
    - Removes null characters (\\x00) which can appear in some BLE implementations
    - Returns an empty string if decoding fails completely
    
    Args:
        v: List of integers (byte values) to convert
        
    Returns:
        Decoded string, or empty string if decoding fails
    """
    try:
        msg = bytes(v).decode("utf-8", errors="ignore")
        return msg.replace("\x00", "")
    except Exception:
        return ""

def _get_adapter_addr() -> str:
    """
    Discover the Bluetooth adapter address from the system using bluetoothctl.
    
    This function:
    - Calls the bluetoothctl system command to list available Bluetooth controllers
    - Parses the output to find the first controller
    - Returns its MAC address (e.g., "5C:F3:70:94:12:34")
    
    Returns:
        The Bluetooth adapter MAC address
        
    Raises:
        RuntimeError: If no Bluetooth adapter is found on the system
    """
    out = subprocess.check_output(["bluetoothctl", "list"], text=True, stderr=subprocess.STDOUT)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Controller "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    raise RuntimeError("No BLE adapter found (bluetoothctl list returned none).")

# ============================================================================
# Data Classes and Main BLE Server
# ============================================================================

@dataclass
class BleCommand:
    """
    Data class representing a single command received from a BLE client.
    
    Attributes:
        raw: The raw command string as received (e.g., "ENABLE=1")
    """
    raw: str


class BleServer:
    """
    BLE Server implementation for the Polar Feeder device.
    
    This class sets up and manages a BLE GATT server using the Nordic UART Service (NUS)
    profile. It handles:
    - Advertising the device over BLE
    - Receiving commands from BLE clients via the RX characteristic
    - Processing commands through a user-provided handler
    - Sending responses back to clients via the TX characteristic
    
    Protocol:
    - Commands are sent by clients as newline-delimited strings
    - The server processes each command and sends back a response
    - Responses are also newline-delimited (one per line)
    
    Example Usage:
        def handle_command(cmd: BleCommand) -> str:
            if cmd.raw == "STATUS":
                return "OK status=idle"
            return "ERR UNKNOWN_COMMAND"
        
        server = BleServer("MyDevice")
        server.set_command_handler(handle_command)
        server.start()
    """
    
    def __init__(self, name: str = "PolarFeeder", adapter_addr: Optional[str] = None):
        """
        Initialize the BLE Server.
        
        Args:
            name: The name to advertise over BLE (appears in BLE scans)
            adapter_addr: Optional Bluetooth adapter address. If None, auto-detects the system's adapter.
        """
        self.name = name
        self.adapter_addr = adapter_addr or _get_adapter_addr()

        # BLE peripheral object (created when start() is called)
        self._p: Optional[peripheral.Peripheral] = None
        
        # User-provided callback function that handles incoming commands
        # Takes a BleCommand and returns a response string
        self._on_command: Optional[Callable[[BleCommand], str]] = None

        # Service and characteristic IDs used internally by bluezero
        self._srv_id = 1      # ID for the primary service
        self._rx_id = 1       # ID for the RX characteristic (receives from client)
        self._tx_id = 2       # ID for the TX characteristic (sends to client)

        # State variables for message handling
        self._tx_value = "BOOT\n"  # Current TX value (what clients read/get notified of)
        self._rx_buf = ""          # Buffer for incomplete messages (waiting for newline)
        self.last_rx_time = time.time()  # Timestamp of last received message

    def set_command_handler(self, fn: Callable[[BleCommand], str]):
        """
        Register a callback function to handle incoming BLE commands.
        
        The handler function receives a BleCommand with the raw command string and should
        return a response string. Common response formats:
        - Success: "ACK ENABLE=1"
        - Error: "ERR OUT_OF_RANGE retract_delay_ms 0 3000"
        - Multiple responses are separated by newlines
        
        Args:
            fn: A callable that takes a BleCommand and returns a response string
        """
        self._on_command = fn

    def _read_tx(self):
        """
        Callback for when a client reads the TX characteristic.
        
        Returns the current TX value as a list of bytes.
        This allows clients to poll for updates even if notifications aren't supported.
        """
        return _str_to_bytes_list(self._tx_value)

    def _on_write_rx(self, value, options):
        """
        Callback handler for when a client writes to the RX characteristic.
        
        This is where incoming commands from BLE clients are processed. The method:
        1. Converts the byte value to a string
        2. Handles escape sequences (e.g., "\\n" -> newline)
        3. Buffers incomplete messages until a newline is received
        4. Processes complete lines by calling the command handler
        5. Sends responses back to the client via notify
        
        Args:
            value: List of bytes written by the client
            options: Dictionary of BLE write options (context-specific)
        """
        chunk = _bytes_list_to_str(value)
        self.last_rx_time = time.time()

        # Keep raw logging useful for debugging BLE communication
        print("[BLE WRITE] chunk:", repr(chunk), "raw:", value, "options:", options, flush=True)

        if not chunk:
            self.notify("ERR EMPTY\n")
            return

        # Normalize common escape sequences that some clients send as literal text
        # e.g. "ENABLE=1\\n" (with backslash-n) -> "ENABLE=1\n" (actual newline)
        chunk = chunk.replace("\\r", "\r").replace("\\n", "\n")

        self._rx_buf += chunk
        responses: List[str] = []

        def process_line(line: str) -> None:
            """
            Process a single complete command line.
            
            Args:
                line: The command string (newlines already removed)
            """
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

        # Step 1: If we have real newlines in the buffer, process all complete lines
        while "\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split("\n", 1)
            process_line(line)

        # Step 2: If there were no newlines at all, treat this write as a full command
        # This makes nRF Connect + MIT App Inventor (which don't always send newlines) behave nicely.
        if not responses and self._rx_buf.strip():
            process_line(self._rx_buf)
            self._rx_buf = ""

        # Send all responses back to the client
        if responses:
            self.notify("\n".join(responses) + "\n")


    def notify(self, text: str):
        """
        Send a message to connected BLE clients via the TX characteristic.
        
        This method:
        - Updates the TX characteristic value (for clients that poll via reads)
        - Attempts to send a BLE notification (for clients that listen to notifications)
        - Gracefully handles different bluezero versions which may have different notify APIs
        
        Args:
            text: The message string to send to clients
        """
        if not self._p:
            return

        self._tx_value = text

        # Try a few possible notify method names depending on bluezero version.
        # Different versions of bluezero may use different method names for notifying clients.
        for method_name in ("notify", "send_notify_event", "notify_characteristic"):
            m = getattr(self._p, method_name, None)
            if callable(m):
                try:
                    m(self._srv_id, self._tx_id)
                    return
                except Exception as e:
                    print(f"[BLE] notify via {method_name} failed: {e}", flush=True)

        # Fallback: no notify method available
        # The TX characteristic value is still updated and available via direct reads,
        # so clients can poll for updates even if notifications aren't supported
        print("[BLE] No notify method available on this bluezero version; TX is readable (poll/read to get updates).", flush=True)


    def start(self):
        """
        Initialize and advertise the BLE server.
        
        This method:
        1. Creates a Peripheral object using the specified BLE adapter
        2. Adds the primary service with UUIDs
        3. Adds the RX characteristic (for receiving client commands)
        4. Adds the TX characteristic (for sending responses to clients)
        5. Publishes the service to make the device discoverable via BLE scans
        
        After calling this, the device will appear in BLE scans and clients can connect.
        """
        print("BLE Server Starting", flush=True)
        self._p = peripheral.Peripheral(adapter_address=self.adapter_addr, local_name=self.name)

        # Add the primary service with the Nordic UART Service UUID
        self._p.add_service(srv_id=self._srv_id, uuid=SERVICE_UUID, primary=True)

        # RX Characteristic: Receives commands from BLE clients
        # Flags "write" and "write-without-response" allow clients to send data without waiting for acknowledgment
        self._p.add_characteristic(
            srv_id=self._srv_id,
            chr_id=self._rx_id,
            uuid=RX_CHAR_UUID,
            flags=["write-without-response"],
            value=_str_to_bytes_list(""),
            notifying=False,
            write_callback=self._on_write_rx,  # Callback triggered when client writes
        )

        # TX Characteristic: Sends responses to BLE clients
        # Flags "notify" and "read" allow clients to receive data via notifications or polling
        self._p.add_characteristic(
            srv_id=self._srv_id,
            chr_id=self._tx_id,
            uuid=TX_CHAR_UUID,
            flags=["notify", "read"],
            value=_str_to_bytes_list(self._tx_value),
            notifying=True,  # Start with notifications enabled
            read_callback=self._read_tx,  # Callback for clients that read instead of listening to notifications
        )

        print("BLE Publishing Now", flush=True)
        # Start a persistent bluetoothctl agent process that stays alive
        # to handle pairing requests automatically. Must be persistent
        # (not one-shot with quit) so it responds immediately when Android
        # initiates pairing at any point after connection.
        try:
            self._agent_proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._agent_proc.stdin.write(b"agent NoInputNoOutput\ndefault-agent\n")
            self._agent_proc.stdin.flush()
            print("[BLE] Persistent auto-pair agent started", flush=True)
        except Exception as e:
            self._agent_proc = None
            print(f"[BLE] Agent start warning: {e}", flush=True)
        self._p.publish()
