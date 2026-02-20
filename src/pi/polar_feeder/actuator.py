import time
import gpiod

class Actuator:
    def __init__(self, chip="/dev/gpiochip0", extend_line=17, retract_line=27, pulse_s=0.2):
        self.chip_path = chip
        self.extend_line = int(extend_line)
        self.retract_line = int(retract_line)
        self.pulse_s = float(pulse_s)

        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.OUTPUT,
            output_value=gpiod.line.Value.INACTIVE
        )

        self._config = {
            self.extend_line: settings,
            self.retract_line: settings,
        }
        self._req = None

    def open(self):
        if self._req is None:
            self._req = gpiod.request_lines(
                self.chip_path,
                consumer="polar_feeder_actuator",
                config=self._config
            )
            self._req.set_value(self.extend_line, gpiod.line.Value.INACTIVE)
            self._req.set_value(self.retract_line, gpiod.line.Value.INACTIVE)

    def close(self):
        if self._req is not None:
            try:
                self._req.set_value(self.extend_line, gpiod.line.Value.INACTIVE)
                self._req.set_value(self.retract_line, gpiod.line.Value.INACTIVE)
            except Exception:
                pass
            self._req.release()
            self._req = None

    def _pulse(self, line: int, duration_s: float | None = None):
        if self._req is None:
            raise RuntimeError("Actuator not opened")
        d = self.pulse_s if duration_s is None else float(duration_s)
        d = max(0.05, d)

        self._req.set_value(line, gpiod.line.Value.ACTIVE)
        time.sleep(d)
        self._req.set_value(line, gpiod.line.Value.INACTIVE)

    def extend(self, duration_s: float | None = None):
        self._pulse(self.extend_line, duration_s)

    def retract(self, duration_s: float | None = None):
        self._pulse(self.retract_line, duration_s)
