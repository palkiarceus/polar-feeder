"""
Vision Module for Polar Feeder

This module handles computer vision processing for polar bear detection and tracking.
It processes YOLO detection data and computes motion metrics for threat assessment.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


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

    # Blend weights for the two motion components.
    # center_motion:  lateral/diagonal movement — reliable, low noise
    # size_change:    direct approach toward camera — noisier due to YOLO
    #                 box sizing instability, but catches what center misses
    # Zoo data showed center_motion dominated under max(); blend ensures
    # size_change always contributes rather than getting masked.
    CENTER_WEIGHT = 0.65
    SIZE_WEIGHT = 0.35

    def __init__(self):
        self.last_detection: Optional[Detection] = None
        self._detection_counter = 0
        self._frames_since_detection = 0  # incremented by mark_no_detection()
        self._last_motion: float = 0.0    # exposed for external debug printing
        self._last_center_motion: float = 0.0  # debug: lateral component
        self._last_size_change: float = 0.0    # debug: approach component

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
        """Compute motion score as a weighted blend of center displacement and box size change.

        Center displacement (weight 0.65) catches lateral/diagonal movement and
        is the more reliable signal — YOLO center coordinates are stable.

        Box size change (weight 0.35) catches direct approach toward the camera.
        Weighted lower because YOLO box dimensions are noisier than center
        coordinates, but it must always contribute so a straight-line approach
        isn't masked when center drift happens to be small.

        Previously used max(center, size) which caused center_motion jitter to
        dominate almost every frame, effectively discarding size_change entirely.
        Zoo data confirmed size_change was contributing ~0% of logged scores.

        Returns motion score in pixel units (same scale as before — thresholds
        don't need to change, just the score now better reflects both axes).
        """
        if self._frames_since_detection >= self.LOST_THRESHOLD:
            self.update(new_detection)
            self._frames_since_detection = 0
            self._last_motion = 0.0
            self._last_center_motion = 0.0
            self._last_size_change = 0.0
            return 0.0

        if self.last_detection is None:
            self.update(new_detection)
            self._frames_since_detection = 0
            self._last_motion = 0.0
            self._last_center_motion = 0.0
            self._last_size_change = 0.0
            return 0.0

        # --- Component 1: center displacement (lateral/diagonal movement) ---
        old_center = self.last_detection.center()
        new_center = new_detection.center()
        dx = new_center[0] - old_center[0]
        dy = new_center[1] - old_center[1]
        center_motion = (dx ** 2 + dy ** 2) ** 0.5

        # --- Component 2: box size change (approach toward camera) ---
        # Diagonal is more stable than area (area grows as square of distance
        # change, making it hypersensitive to small size shifts).
        old_diag = (self.last_detection.width() ** 2 + self.last_detection.height() ** 2) ** 0.5
        new_diag = (new_detection.width() ** 2 + new_detection.height() ** 2) ** 0.5
        size_change = abs(new_diag - old_diag)

        # Weighted blend: both components always contribute.
        motion = self.CENTER_WEIGHT * center_motion + self.SIZE_WEIGHT * size_change

        self.update(new_detection)
        self._frames_since_detection = 0
        self._last_motion = motion
        self._last_center_motion = center_motion
        self._last_size_change = size_change
        return motion


class SensorFusion:
    """Combine radar and vision info into a fused threat signal."""

    # When radar distance is unavailable, multiply base threshold by this
    # factor before comparing against vision motion. Without spatial context
    # the motion score has no distance grounding, so we require stronger
    # evidence from vision before calling a threat. Zoo data showed fused_threat
    # firing on motion as low as 1.6px when radar was absent — almost certainly
    # YOLO jitter, not real movement.
    NO_RADAR_THRESHOLD_MULTIPLIER = 1.5

    # Minimum fraction of base_motion_threshold the adaptive curve will go.
    # 20% (original) put the floor at ~4px on a 20px base — within YOLO jitter
    # range at close range. Zoo data showed mean motion of 12.5px even on a
    # still bear at <0.5m. 40% (~8px) stays above that noise floor.
    ADAPTIVE_FLOOR_FRACTION = 0.4

    def __init__(
        self,
        base_motion_threshold: float = 20.0,
        detection_distance_m: float = 3.0,
        feeding_distance_m: float = 0.5,
    ):
        """
        Initialize sensor fusion with distance-adaptive parameters.

        Args:
            base_motion_threshold: Motion threshold at maximum detection distance.
            detection_distance_m:  Beyond this distance, threshold is infinite (ignore).
            feeding_distance_m:    At or below this distance, threshold is at floor.
        """
        self.base_motion_threshold = base_motion_threshold
        self.detection_distance_m = detection_distance_m
        self.feeding_distance_m = feeding_distance_m
        self.last_radar_ts: Optional[float] = None
        self.last_cv_ts: Optional[float] = None

    def _adaptive_motion_threshold(self, radar_distance_m: float | None) -> float:
        """Calculate distance-adaptive motion threshold.

        No radar:  Returns base * NO_RADAR_THRESHOLD_MULTIPLIER (conservative).
        Too far:   Returns infinity (bear out of range, ignore).
        At floor:  Returns base * ADAPTIVE_FLOOR_FRACTION.
        In range:  Square-root interpolation between base and floor.

        Square-root curve rationale: pixel displacement scales roughly with
        1/distance (half the distance = double the pixels for same movement),
        so a linear curve underestimates how much the threshold should drop
        at close range. sqrt() makes it drop faster up close while staying
        high at longer ranges where the bear still needs to make a big move
        to register.
        """
        floor = self.base_motion_threshold * self.ADAPTIVE_FLOOR_FRACTION

        if radar_distance_m is None:
            return self.base_motion_threshold * self.NO_RADAR_THRESHOLD_MULTIPLIER

        if radar_distance_m > self.detection_distance_m:
            return float('inf')

        if radar_distance_m <= self.feeding_distance_m:
            return floor

        distance_range = self.detection_distance_m - self.feeding_distance_m
        threshold_range = self.base_motion_threshold - floor

        # progress: 0.0 at detection_distance (far), 1.0 at feeding_distance (close)
        # sqrt curve: drops slowly at first, then faster as bear gets close
        progress = ((self.detection_distance_m - radar_distance_m) / distance_range) ** 0.5
        adaptive_threshold = self.base_motion_threshold - (progress * threshold_range)

        return max(adaptive_threshold, floor)

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

    def fused_threat(
        self,
        radar_threat: bool,
        motion_magnitude: float,
        radar_distance_m: float | None = None,
    ) -> bool:
        """Combine radar and vision threats using distance-adaptive motion threshold.

        Args:
            radar_threat:      Boolean from radar sudden-distance-change detection.
            motion_magnitude:  Vision motion score from VisionTracker.compute_motion().
            radar_distance_m:  Current radar distance. None = radar absent/unavailable.

        Returns:
            True if any threat detected (radar OR vision).
        """
        adaptive_threshold = self._adaptive_motion_threshold(radar_distance_m)
        vision_threat = motion_magnitude >= adaptive_threshold
        return radar_threat or vision_threat
