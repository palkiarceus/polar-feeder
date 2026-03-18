import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import serial


RADAR_RE = re.compile(
    r"bin=(?P<bin>\d+)\s+dist\s*=\s*(?P<dist>[0-9.]+)\s*m\s+ts\s*=\s*(?P<ts>\d+)"
)


@dataclass
class RadarReading:
    raw_line: str = ""
    bin_index: Optional[int] = None
    distance_m: Optional[float] = None
    timestamp: Optional[int] = None
    speed_mps: Optional[float] = None
    threat: bool = False
    valid: bool = False
    seq: int = 0


class RadarReader:
    def __init__(
        self,
        port: str,
        baud: int = 115200,
        timeout_s: float = 0.1,
        distance_jump_m: float = 0.50,
    ):
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.distance_jump_m = distance_jump_m

        self._ser = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self._latest = RadarReading()
        self._prev_dist = None
        self._prev_ts = None
        self._seq = 0

    def start(self):
        self._ser = serial.Serial(self.port, baudrate=self.baud, timeout=self.timeout_s)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._ser and self._ser.is_open:
            self._ser.close()
            
    def reset_baseline(self):
        with self._lock:
            self._prev_dist = None
            self._prev_ts = None
            self._latest = RadarReading(seq=self._seq)
            
    def get_latest(self) -> RadarReading:
        with self._lock:
            return RadarReading(
                raw_line=self._latest.raw_line,
                bin_index=self._latest.bin_index,
                distance_m=self._latest.distance_m,
                timestamp=self._latest.timestamp,
                speed_mps=self._latest.speed_mps,
                threat=self._latest.threat,
                valid=self._latest.valid,
                seq=self._latest.seq,
            )

    def _run(self):
        while not self._stop.is_set():
            try:
                line = self._ser.readline().decode(errors="ignore").strip()
                if not line:
                    continue

                reading = self._parse_line(line)

                with self._lock:
                    self._latest = reading

            except Exception as e:
                print(f"[RADAR] read error: {type(e).__name__}: {e}", flush=True)
                time.sleep(0.1)

    def _parse_line(self, line: str) -> RadarReading:
        m = RADAR_RE.search(line)
        if not m:
            return RadarReading(raw_line=line, valid=False, seq=self._seq)

        bin_index = int(m.group("bin"))
        distance_m = float(m.group("dist"))
        timestamp = int(m.group("ts"))

        speed_mps = None
        threat = False

        if self._prev_dist is not None:
            delta_d = abs(distance_m - self._prev_dist)

            if delta_d >= self.distance_jump_m:
                threat = True

            if self._prev_ts is not None and timestamp > self._prev_ts:
                delta_t = timestamp - self._prev_ts
                # speed_mps = delta_d / delta_t
                speed_mps = None

        self._prev_dist = distance_m
        self._prev_ts = timestamp

        self._seq += 1

        return RadarReading(
            raw_line=line,
            bin_index=bin_index,
            distance_m=distance_m,
            timestamp=timestamp,
            speed_mps=speed_mps,
            threat=threat,
            valid=True,
            seq=self._seq,
        )
