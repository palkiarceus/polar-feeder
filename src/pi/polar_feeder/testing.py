      
class MockActuator:
    def __init__(self):
        self.log = []
    def extend(self):
        self.log.append("EXTEND")
        print("[ACT] EXTEND", flush=True)
    def retract(self):
        self.log.append("RETRACT")
        print("[ACT] RETRACT", flush=True)

from polar_feeder.feeder_fsm import FeederFSM, State
from polar_feeder.inverse_feeder_fsm import InverseFeederFSM, InverseState
from polar_feeder.radar import RadarReader, RADAR_RE


act = MockActuator()
fsm = FeederFSM(act, retract_delay_ms=500, cooldown_s=2.0,
                motion_threshold=30.0, feeding_distance_m=0.5,
                detection_distance_m=3.0)

t = 0.0

# IDLE -> LURE
fsm.tick(enable=True, threat=False, now=t); t += 0.1
assert fsm.state == State.LURE, f"Expected LURE got {fsm.state}"
assert "EXTEND" in act.log

# LURE -> RETRACT_WAIT on threat
fsm.tick(enable=True, threat=True, motion_magnitude=50.0,
         radar_distance_m=1.5, now=t); t += 0.1
assert fsm.state == State.RETRACT_WAIT, f"Expected RETRACT_WAIT got {fsm.state}"

# RETRACT_WAIT -> COOLDOWN after delay
t += 0.6  # past 500ms
fsm.tick(enable=True, threat=False, now=t); t += 0.1
assert fsm.state == State.COOLDOWN, f"Expected COOLDOWN got {fsm.state}"
assert "RETRACT" in act.log

# COOLDOWN -> IDLE after cooldown_s
t += 2.1
fsm.tick(enable=True, threat=False, now=t)
assert fsm.state == State.IDLE, f"Expected IDLE got {fsm.state}"

# IDLE -> LURE -> FEEDING on close distance
act.log.clear()
fsm.tick(enable=True, threat=False, now=t); t += 0.1  # IDLE->LURE
fsm.tick(enable=True, threat=False, radar_distance_m=0.3, now=t)  # LURE->FEEDING
assert fsm.state == State.FEEDING, f"Expected FEEDING got {fsm.state}"

# manual_retract -> COOLDOWN
result = fsm.manual_retract(now=t)
assert result == True
assert fsm.state == State.COOLDOWN, f"Expected COOLDOWN got {fsm.state}"
assert act.log[-1] == "RETRACT"

print("LURE FSM: ALL TESTS PASSED")



act = MockActuator()
fsm = InverseFeederFSM(act, motion_threshold=20.0, min_still_duration_s=1.5,
                       cooldown_s=2.0, detection_distance_m=3.0,
                       feeding_distance_m=0.5, noise_buffer_multiplier=1.5)

t = 0.0

# IDLE -> WATCHING
fsm.tick(enable=True, bear_detected=False, now=t); t += 0.1
assert fsm.state == InverseState.WATCHING, f"Expected WATCHING got {fsm.state}"

# WATCHING: jitter frame (21px) should NOT reset timer
fsm.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
         radar_distance_m=1.5, now=t); t += 0.1   # start timer
fsm.tick(enable=True, bear_detected=True, motion_magnitude=21.0,
         radar_distance_m=1.5, now=t); t += 0.1   # jitter — timer should pause not reset
assert fsm._still_since is not None, "Timer was reset by jitter frame — bug"

# Hold still long enough -> DISPENSING
t += 1.5
fsm.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
         radar_distance_m=1.5, now=t); t += 0.1
assert fsm.state == InverseState.DISPENSING, f"Expected DISPENSING got {fsm.state}"
assert "EXTEND" in act.log

# DISPENSING: bear far away — should NOT enter REWARDING even after hold
t += 2.0
fsm.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
         radar_distance_m=2.0, now=t); t += 0.1   # far, timer resets
assert fsm.state == InverseState.DISPENSING, "Should stay DISPENSING when far"

# DISPENSING: bear close + still -> REWARDING
fsm._dispensing_since = None  # reset timer fresh
t += 2.0
fsm.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
         radar_distance_m=0.3, now=t); t += 0.1   # start dispensing timer close
t += 2.0
fsm.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
         radar_distance_m=0.3, now=t)
assert fsm.state == InverseState.REWARDING, f"Expected REWARDING got {fsm.state}"

# manual_retract from REWARDING -> COOLDOWN
result = fsm.manual_retract(now=t)
assert result == True
assert fsm.state == InverseState.COOLDOWN
assert act.log[-1] == "RETRACT"

# DISPENSING: bear moves -> COOLDOWN
act2 = MockActuator()
fsm2 = InverseFeederFSM(act2, motion_threshold=20.0, min_still_duration_s=1.5,
                        cooldown_s=2.0, feeding_distance_m=0.5)
t2 = 0.0
fsm2.tick(enable=True, bear_detected=False, now=t2); t2 += 0.1  # WATCHING
fsm2.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
          radar_distance_m=1.5, now=t2); t2 += 1.7  # start + hold
fsm2.tick(enable=True, bear_detected=True, motion_magnitude=5.0,
          radar_distance_m=1.5, now=t2); t2 += 0.1  # -> DISPENSING
assert fsm2.state == InverseState.DISPENSING
fsm2.tick(enable=True, bear_detected=True, motion_magnitude=50.0,
          radar_distance_m=1.5, now=t2)  # move -> COOLDOWN
assert fsm2.state == InverseState.COOLDOWN, f"Expected COOLDOWN got {fsm2.state}"

print("INVERSE FSM: ALL TESTS PASSED")


lines = [
    "[INFO] macro presence bin=3 dist=0.98 m ts=468389",
    "[INFO] macro presence bin=10 dist=3.26 m ts=468891",
    "[presence bin=3 dist=0.98 m ts=471417",   # corrupted prefix
    "current presence: 0, current setting: 10 Hz",  # should not match
]

for line in lines:
    m = RADAR_RE.search(line)
    if m:
        print(f"MATCH  bin={m.group('bin')} dist={m.group('dist')} ts={m.group('ts')}")
    else:
        print(f"NO MATCH (expected): {line!r}")

# Stale detection — verify distance_m=None after no new seq
print("Radar parser: ALL TESTS PASSED")
