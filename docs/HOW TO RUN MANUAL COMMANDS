cd /home/arcticproject/Desktop/polar_bear_project_github/polar-feeder
bash src/pi/scripts/run.sh --ble-test --config /etc/polar_feeder/config.json

service logs: 
journalctl -u polar-feeder -f

github commands:
cd /home/arcticproject/Desktop/polar_bear_project_github/polar-feeder
git branch
git status
git add .
git commit -m "Message"
git push origin Deadend
git status

UART PORT: /dev/ttyAMA0
USB PORT: /dev/ttyACD0



READING FROM THE RADAR BOARD

sudo systemctl stop polar-feeder

stty -F /dev/ttyAMA0 115200 raw -echo

# Start a fixed-length read first
(dd if=/dev/ttyAMA0 bs=1 count=6 status=none | hexdump -C) &
sleep 0.1

echo -n "ABC123" > /dev/ttyAMA0
wait

sudo systemctl start polar-feeder




ensure tx-rx, common ground


./.venv/bin/python - <<'PY'
import serial

port = "/dev/ttyAMA0"
baud = 115200

ser = serial.Serial(port, baudrate=baud, timeout=1)

print(f"Listening on {port} @ {baud}...")
print("Press Ctrl+C to stop\n")

try:
    while True:
        data = ser.read(64)
        if data:
            print(data)
except KeyboardInterrupt:
    print("\nStopped.")
    ser.close()
PY




GPIOs: 17 



sudo systemctl restart bluetooth
bash src/pi/scripts/run.sh --ble-test --config /etc/polar_feeder/config.json


CAMERA TEST: 
python -m polar_feeder.yolo_detect --model yolov8n.pt --source picamera0 --skip 1 --thresh 0.3


make sure if running tests you stop the service: 
sudo systemctl start polar-feeder
-we need to update it later on


TO RUN THE INTEGRATION:
first, move into the root repo folder
then activate the venv
then move to src/pi and do these
sudo systemctl restart bluetooth
bash scripts/run.sh --ble-test --config /etc/polar_feeder/config.json

remember for this one its the etc json the test is using, NOT the one that is from the src/pi config or the root repo folder
