"""
Actuator control via GPIO (gpiod v2).

Drives RF transmitter input or relay pin.
"""

import time
import gpiod


class Actuator:
    def __init__(self, chip="/dev/gpiochip0", line=17):
        self.chip_path = chip
        self.line = line

        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.OUTPUT,
            output_value=gpiod.line.Value.INACTIVE
        )

        self._config = {self.line: settings}
        self._req = None

    def open(self):
        if self._req is None:
            self._req = gpiod.request_lines(
                self.chip_path,
                consumer="polar_feeder_actuator",
                config=self._config
            )

    def close(self):
        if self._req is not None:
            self._req.release()
            self._req = None

    def pulse(self, duration=0.2):
        """
        Send HIGH pulse for duration (seconds).
        """
        if self._req is None:
            raise RuntimeError("Actuator not opened")

        self._req.set_value(self.line, gpiod.line.Value.ACTIVE)
        time.sleep(duration)
        self._req.set_value(self.line, gpiod.line.Value.INACTIVE)

    def extend(self):
        # customize if needed
        self.pulse(0.2)

    def retract(self):
        # customize if needed
        self.pulse(0.2)
