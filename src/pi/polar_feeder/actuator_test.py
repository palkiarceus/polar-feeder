from polar_feeder.actuator import Actuator
import time

a = Actuator(extend_line=17, retract_line=27, pulse_s=0.2)
a.open()

print("EXTEND x3")
for _ in range(3):
    a.extend()
    time.sleep(0.5)

print("RETRACT x3")
for _ in range(3):
    a.retract()
    time.sleep(0.5)

a.close()
