"""
Radar Reader Module for Polar Feeder

This module reads and parses RF radar sensor data from a serial port. The radar device
sends distance measurements and motion detection to help identify when animals are
approaching the feeder.

Key Features:
- Reads radar data over serial communication
- Parses radar-specific output format (bin index, distance, timestamp)
- Detects threats based on distance changes exceeding a threshold
- Thread-safe data access with locks
- Configurable sensitivity and serial parameters

The RadarReading dataclass encapsulates all information from a single radar reading.
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import serial


# Regular expression to parse radar output format
# Expected format: "bin=<index> dist=<distance>m ts=<timestamp>"
# Example: "bin=5 dist=2.34m ts=1234567890"
RADAR_RE = re.compile(
    r"bin=(?P<bin>\d+)\s+dist\s*=\s*(?P<dist>[0-9.]+)\s*m\s+ts\s*=\s*(?P<ts>\d+)"
)


@dataclass
class RadarReading:
    """
    Data class representing a single radar measurement.
    
    Attributes:
        raw_line: The complete raw string received from the radar device
        bin_index: The radar bin/zone index (which direction/range the detection came from)
        distance_m: Measured distance in meters
        timestamp: Radar device timestamp (device-specific units, usually milliseconds)
        speed_mps: Calculated speed in meters per second (currently unused/None)
        threat: Boolean indicating if this reading represents a threat (sudden distance change)
        valid: Boolean indicating if the line was successfully parsed
        seq: Sequence number of this reading (increments with each valid parse)
    """
    raw_line: str = ""
    bin_index: Optional[int] = None
    distance_m: Optional[float] = None
    timestamp: Optional[int] = None
    speed_mps: Optional[float] = None
    threat: bool = False
    valid: bool = False
    seq: int = 0  # Fixed: removed duplicate


class RadarReader:
    """
    Thread-based radar sensor reader.
    
    This class manages serial communication with a radar sensor and parses the incoming
    data. It runs a background thread that continuously reads from the serial port and
    updates the latest reading. Clients can poll get_latest() to retrieve the current
    measurement without blocking.
    
    Thread Safety:
    - Uses a lock (_lock) to protect access to _latest reading
    - Safe to call from multiple threads
    - get_latest() returns a copy to avoid race conditions
    
    Example Usage:
        radar = RadarReader(port="/dev/ttyAMA0", baud=115200)
        radar.start()
        
        while True:
            reading = radar.get_latest()
            if reading.valid and reading.threat:
                print(f"Threat detected: {reading.distance_m}m away")
            time.sleep(0.1)
        
        radar.stop()
    """
    
    def __init__(
        self,
        port: str,
        baud: int = 115200,
        timeout_s: float = 0.1,
        distance_jump_m: float = 0.50,  # Fixed: removed duplicate
    ):
        """
        Initialize the radar reader.
        
        Args:
            port: Serial port device (e.g., "/dev/ttyAMA0" on Raspberry Pi, "COM3" on Windows)
            baud: Serial communication speed in bits per second. Default: 115200
            timeout_s: Serial read timeout in seconds. Lower values = more responsive but more CPU. Default: 0.1
            distance_jump_m: Threshold in meters for detecting sudden distance changes (threats).
                           If distance changes by more than this, it's marked as threat. Default: 0.50m
        """
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.distance_jump_m = distance_jump_m

        # Serial port object (initialized when start() is called)
        self._ser = None
        # Background thread object
        self._thread = None
        # Event to signal the thread to stop
        self._stop = threading.Event()
        # Lock for thread-safe access to _latest reading
        self._lock = threading.Lock()

        # Latest reading (updated by background thread)
        self._latest = RadarReading()
        # Previous distance for calculating delta (threat detection)
        self._prev_dist = None
        # Previous timestamp for calculating delta
        self._prev_ts = None
        # Sequence counter for tracking readings
        self._seq = 0

    def start(self):
        self._ser = serial.Serial(
            self.port,
            baudrate=self.baud,
            timeout=self.timeout_s,
            rtscts=False,
            dsrdtr=False,
        )
        # Flush any stale data from previous sessions
        self._ser.reset_input_buffer()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """
        Stop the background reader thread and close the serial port.
        
        Signals the thread to stop and waits up to 1 second for it to finish.
        Closes the serial port to release the device resource.
        
        Thread Safe: Yes - safe to call from any thread.
        """
        self._stop.set()  # Signal thread to stop
        if self._thread:
            self._thread.join(timeout=1.0)  # Wait for thread to finish
        if self._ser and self._ser.is_open:
            self._ser.close()  # Release serial port
            
    def reset_baseline(self):
        """
        Reset the baseline distance measurement for threat detection.
        
        Use this to clear the previous distance after a threat is detected,
        preventing the next movement from triggering another threat alert.
        
        Thread Safe: Yes - uses internal lock.
        """
        with self._lock:
            self._prev_dist = None
            self._prev_ts = None
            self._latest = RadarReading(seq=self._seq)
            
    def get_latest(self) -> RadarReading:
        """
        Retrieve the latest radar reading.
        
        Returns a copy of the current reading to ensure thread safety.
        This is safe to call from any thread without blocking.
        
        Returns:
            RadarReading object with the latest measurement
            
        Thread Safe: Yes - uses internal lock and returns a copy.
        """
        with self._lock:
            return RadarReading(
                raw_line=self._latest.raw_line,
                bin_index=self._latest.bin_index,
                distance_m=self._latest.distance_m,
                timestamp=self._latest.timestamp,
                speed_mps=self._latest.speed_mps,
                threat=self._latest.threat,
                valid=self._latest.valid,
                seq=self._latest.seq,  # Fixed: removed duplicate
            )

    def _run(self):
        """
        Main loop of the background reader thread.
        
        This runs in a separate thread and continuously:
        1. Reads lines from the serial port
        2. Parses the radar data
        3. Updates _latest with the new reading
        4. Runs until _stop event is set
        
        Errors are caught and logged; the thread continues running.
        """
        while not self._stop.is_set():
            try:
                raw = self._ser.readline()
                line = raw.decode(errors="ignore").strip()
                if not line:
                    continue

                print(f"[RADAR RAW] {line!r}", flush=True)

                # Skip fragments — valid lines always contain 'bin=' and 'dist='
                if "bin=" not in line or "dist=" not in line:
                    continue

                reading = self._parse_line(line)
                with self._lock:
                    self._latest = reading

            except Exception as e:
                print(f"[RADAR] read error: {type(e).__name__}: {e}", flush=True)
                with self._lock:
                    self._latest = RadarReading(valid=False, seq=self._seq)
                time.sleep(0.1)

    def _parse_line(self, line: str) -> RadarReading:
        """
        Parse a single line of radar output.
        
        Extracts bin index, distance, and timestamp using the RADAR_RE regex.
        Compares distance to previous measurement to detect threats (sudden distance changes).
        
        Args:
            line: Raw line string from the radar device
            
        Returns:
            RadarReading object with parsed data and threat status
        """
        # Try to match the expected radar format
        m = RADAR_RE.search(line)
        if not m:
            # Line didn't match expected format
            return RadarReading(raw_line=line, valid=False, seq=self._seq)

        # Extract the three main fields
        bin_index = int(m.group("bin"))
        distance_m = float(m.group("dist"))
        timestamp = int(m.group("ts"))

        # Initialize threat and speed (not currently used)
        speed_mps = None
        threat = False

        # Check for sudden distance changes (threat detection)
        if self._prev_dist is not None:
            # Calculate change in distance since last reading
            delta_d = abs(distance_m - self._prev_dist)

            # Threshold comparison: if distance jumped more than distance_jump_m, it's a threat
            if delta_d >= self.distance_jump_m:
                threat = True

            # Calculate speed (currently unused, but infrastructure is here)
            if self._prev_ts is not None and timestamp > self._prev_ts:
                delta_t = timestamp - self._prev_ts
                # speed_mps = delta_d / delta_t  # Currently disabled
                speed_mps = None

        # Update baseline for next comparison
        self._prev_dist = distance_m
        self._prev_ts = timestamp

        # Increment sequence counter
        self._seq += 1

        # Return the complete reading
        return RadarReading(
            raw_line=line,
            bin_index=bin_index,
            distance_m=distance_m,
            timestamp=timestamp,
            speed_mps=speed_mps,
            threat=threat,
            valid=True,
            seq=self._seq,  # Fixed: removed duplicate
        )
