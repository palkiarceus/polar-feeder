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

    # Number of consecutive missed inference frames before we consider
    # the bear "lost" and reset the motion baseline. When the bear
    # reappears after being lost, we return 0.0 instead of a huge
    # spurious delta caused by the gap in tracking.
    LOST_THRESHOLD = 5

    def __init__(self):
        self.last_detection: Optional[Detection] = None
        self._detection_counter = 0
        self._frames_since_detection = 0  # incremented by mark_no_detection()
        self._last_motion: float = 0.0    # exposed for external debug printing

    def mark_no_detection(self) -> None:
        """Call this each inference frame when no bear is detected.

        Increments the lost-frame counter so that if the bear reappears
        after LOST_THRESHOLD missed frames, compute_motion() resets the
        baseline instead of reporting a phantom large displacement.
        """
        self._frames_since_detection += 1

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

        Returns 0.0 on first detection or after bear was lost for
        LOST_THRESHOLD frames, to avoid phantom spikes on reacquisition.
        Returns larger values for faster movement.
        """
        # Bear was lost long enough that the last position is stale —
        # reset baseline so we don't report a huge phantom displacement.
        if self._frames_since_detection >= self.LOST_THRESHOLD:
            self.update(new_detection)
            self._frames_since_detection = 0
            self._last_motion = 0.0
            return 0.0

        # First detection ever
        if self.last_detection is None:
            self.update(new_detection)
            self._frames_since_detection = 0
            self._last_motion = 0.0
            return 0.0

        old_center = self.last_detection.center()
        new_center = new_detection.center()

        dx = new_center[0] - old_center[0]
        dy = new_center[1] - old_center[1]
        motion = (dx ** 2 + dy ** 2) ** 0.5

        self.update(new_detection)
        self._frames_since_detection = 0
        self._last_motion = motion
        return motion


class SensorFusion:
    """Combine radar and vision info into a fused threat signal."""

    def __init__(self, base_motion_threshold: float = 20.0, detection_distance_m: float = 3.0, feeding_distance_m: float = 0.5):
        """
        Initialize sensor fusion with distance-adaptive parameters.
        
        Args:
            base_motion_threshold: Base motion threshold at maximum detection distance
            detection_distance_m: Maximum distance for threat detection
            feeding_distance_m: Distance for feeding state transition
        """
        self.base_motion_threshold = base_motion_threshold
        self.detection_distance_m = detection_distance_m
        self.feeding_distance_m = feeding_distance_m
        self.last_radar_ts: Optional[float] = None
        self.last_cv_ts: Optional[float] = None

    def _adaptive_motion_threshold(self, radar_distance_m: float | None) -> float:
        """
        Calculate distance-adaptive motion threshold (same logic as FSM).
        
        Returns infinity if distance unknown or bear too far away.
        """
        if radar_distance_m is None:
            return self.base_motion_threshold
            
        if radar_distance_m > self.detection_distance_m:
            return float('inf')
            
        if radar_distance_m <= self.feeding_distance_m:
            return self.base_motion_threshold * 0.2
            
        # Linear interpolation
        distance_range = self.detection_distance_m - self.feeding_distance_m
        threshold_range = self.base_motion_threshold - (self.base_motion_threshold * 0.2)
        progress = (self.detection_distance_m - radar_distance_m) / distance_range
        adaptive_threshold = self.base_motion_threshold - (progress * threshold_range)
        
        return max(adaptive_threshold, self.base_motion_threshold * 0.2)

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

    def fused_threat(self, radar_threat: bool, motion_magnitude: float, radar_distance_m: float | None = None) -> bool:
        """
        Combine radar and vision threats using distance-adaptive motion threshold.
        
        Args:
            radar_threat: Boolean from radar sudden distance change detection
            motion_magnitude: Vision motion magnitude
            radar_distance_m: Current radar distance for adaptive thresholding
            
        Returns:
            True if any threat detected (radar or vision)
        """
        # Get adaptive motion threshold based on distance
        adaptive_threshold = self._adaptive_motion_threshold(radar_distance_m)
        
        # Vision threat: motion exceeds adaptive threshold
        vision_threat = motion_magnitude >= adaptive_threshold
        
        return radar_threat or vision_threat
