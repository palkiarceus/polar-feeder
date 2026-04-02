"""
Vision Module for Polar Feeder

This module handles computer vision processing for polar bear detection and tracking.
It processes YOLO detection data and computes motion metrics for threat assessment.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple


@dataclass
class Detection:
    """Represents a single YOLO detection."""
    detection_id: int
    timestamp: float
    ymin: float
    ymax: float
    xmin: float
    xmax: float

    def width(self) -> float:
        """Get bounding box width."""
        return self.xmax - self.xmin

    def height(self) -> float:
        """Get bounding box height."""
        return self.ymax - self.ymin

    def center(self) -> Tuple[float, float]:
        """Get bounding box center coordinates."""
        return ((self.xmin + self.xmax) / 2.0, (self.ymin + self.ymax) / 2.0)

    def area(self) -> float:
        """Get bounding box area."""
        return self.width() * self.height()


class VisionTracker:
    """Tracks detections over time and computes movement deltas."""

    def __init__(self):
        self.last_detection: Optional[Detection] = None
        self._detection_counter = 0

    def parse_line(self, line: str) -> Optional[Detection]:
        """Parse a detection line into Detection instance.

        Expected format (CSV):
            <det_id>,<timestamp>,<ymin>,<ymax>,<xmin>,<xmax>
        Example:
            1,1690001123.45,50,200,30,180
        """
        if not line or not line.strip():
            return None

        parts = [p.strip() for p in line.strip().split(',')]
        if len(parts) != 6:
            raise ValueError(f"Malformed CSV detection line: {line!r}")

        det = Detection(
            detection_id=int(parts[0]),
            timestamp=float(parts[1]),
            ymin=float(parts[2]),
            ymax=float(parts[3]),
            xmin=float(parts[4]),
            xmax=float(parts[5]),
        )

        return det

    def parse_yolo_output(self, output_text: str, timestamp: float | None = None) -> Optional[Detection]:
        """Parse YOLO output.txt format into Detection instance.

        Expected format:
            Detection number: <id>
            Time: <timestamp>
            Xmin = <xmin>
            Xmax = <xmax>
            Ymin = <ymin>
            Ymax = <ymax>

        Args:
            output_text: The raw text from YOLO output.txt (can be single detection or multiple)
            timestamp: Optional override for timestamp. If None, uses value from output.

        Returns:
            Detection object or None if parsing fails
        """
        try:
            # Parse each field from the output format
            detection_id = None
            timestamp_parsed = timestamp
            xmin = None
            xmax = None
            ymin = None
            ymax = None

            for line in output_text.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue

                if line.startswith("Detection number:"):
                    detection_id = int(line.split(':')[1].strip())
                elif line.startswith("Time:"):
                    timestamp_parsed = float(line.split(':')[1].strip())
                elif line.startswith("Xmin"):
                    xmin = float(line.split('=')[1].strip())
                elif line.startswith("Xmax"):
                    xmax = float(line.split('=')[1].strip())
                elif line.startswith("Ymin"):
                    ymin = float(line.split('=')[1].strip())
                elif line.startswith("Ymax"):
                    ymax = float(line.split('=')[1].strip())

            # Validate all required fields were parsed
            if any(v is None for v in [detection_id, timestamp_parsed, xmin, xmax, ymin, ymax]):
                return None

            det = Detection(
                detection_id=detection_id,
                timestamp=timestamp_parsed,
                ymin=ymin,
                ymax=ymax,
                xmin=xmin,
                xmax=xmax,
            )

            return det
        except (ValueError, IndexError, AttributeError):
            return None

    def update(self, detection: Detection) -> None:
        """Update tracker with latest detection."""
        self.last_detection = detection

    def compute_motion(self, new_detection: Detection) -> float:
        """Compute motion score (Euclidean movement of bounding-box center).

        Returns large values for more movement.
        """
        if self.last_detection is None:
            self.update(new_detection)
            return 0.0

        old_center = self.last_detection.center()
        new_center = new_detection.center()

        dx = new_center[0] - old_center[0]
        dy = new_center[1] - old_center[1]

        self.update(new_detection)

        return (dx ** 2 + dy ** 2) ** 0.5


class SensorFusion:
    """Combine radar and vision info into a fused threat signal."""

    def __init__(self, motion_threshold: float = 20.0):
        self.motion_threshold = motion_threshold
        self.last_radar_ts: Optional[float] = None
        self.last_cv_ts: Optional[float] = None

    def update_radar(self, radar_ts: float) -> None:
        """Update with latest radar timestamp."""
        self.last_radar_ts = radar_ts

    def update_vision(self, vision_ts: float) -> None:
        """Update with latest vision timestamp."""
        self.last_cv_ts = vision_ts

    def in_sync(self, window_s: float = 0.5) -> bool:
        """Check if radar and vision timestamps are synchronized."""
        if self.last_radar_ts is None or self.last_cv_ts is None:
            return False
        return abs(self.last_radar_ts - self.last_cv_ts) <= window_s

    def fused_threat(self, radar_threat: bool, motion_magnitude: float) -> bool:
        """Combine radar and vision threats."""
        return radar_threat or (motion_magnitude >= self.motion_threshold) 