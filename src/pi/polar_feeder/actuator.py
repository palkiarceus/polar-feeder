# src/pi/polar_feeder/actuator.py
from dataclasses import dataclass
from polar_feeder.transmittingfunc import transmit1, transmit2, transmitwithdelay

@dataclass
class Actuator:
    retract_delay_s: float = 0.0

    def open(self) -> None:
        # nothing persistent needed for lgpio approach
        return

    def close(self) -> None:
        return

    def extend(self, duration_s=None) -> None:
        transmit1()

    def retract(self, duration_s=None) -> None:
        transmit2()

    def extend_then_retract(self, delay_s: float | None = None) -> None:
        d = self.retract_delay_s if delay_s is None else float(delay_s)
        transmitwithdelay(d)
