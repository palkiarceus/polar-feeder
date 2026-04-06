import os
import sys
import argparse
import glob
import time
import threading

import cv2
import numpy as np
from ultralytics import YOLO

from polar_feeder.config.loader import load_config
from polar_feeder.actuator import Actuator
from polar_feeder.feeder_fsm import FeederFSM
from polar_feeder.vision import VisionTracker

# Global variables for detect_frame function
_model = None
_vision_tracker = None
_cfg = None

def init_yolo_for_ble(model_path="yolov8n.pt"):
    """Initialize YOLO model and vision tracker for BLE mode."""
    global _model, _vision_tracker, _cfg
    if _model is None:
        _cfg = load_config('config/config.example.json')
        _model = YOLO(model_path, task='detect')
        _vision_tracker = VisionTracker()
        print(f"[YOLO] Initialized model {model_path} for BLE mode")

def detect_frame(frame):
    """Process a single frame and return (threat, motion) tuple for FSM."""
    global _model, _vision_tracker, _cfg
    if _model is None or _vision_tracker is None:
        raise RuntimeError("YOLO not initialized. Call init_yolo_for_ble() first.")
    
    # Run inference
    results = _model(frame, classes=[21], verbose=False)  # Class 21 = bear
    detections = results[0].boxes
    
    threat = False
    motion = 0.0
    
    if len(detections) > 0:
        # Get first detection (assuming single bear)
        detection = detections[0]
        xyxy = detection.xyxy.cpu().numpy().squeeze()
        xmin, ymin, xmax, ymax = xyxy.astype(int)
        
        # Build YOLO output block for parser
        yolo_block = (
            f"Detection number: 1\n"
            f"Time: {time.perf_counter()}\n"
            f"Xmin = {xmin}\n"
            f"Xmax = {xmax}\n"
            f"Ymin = {ymin}\n"
            f"Ymax = {ymax}\n"
        )
        
        # Parse and compute motion
        det = _vision_tracker.parse_yolo_output(yolo_block)
        if det is not None:
            motion = _vision_tracker.compute_motion(det)
            threat = motion >= _cfg.vision.motion_threshold
    
    return threat, motion

# ===== Threaded PiCamera frame grabber =====
# Decouples camera capture from inference so the main loop never
# blocks waiting on the camera sensor. The background thread always
# keeps the latest frame hot.
class PiCameraGrabber:
    def __init__(self, cap):
        self.cap = cap
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            frame_bgra = self.cap.capture_array()
            frame = cv2.cvtColor(np.copy(frame_bgra), cv2.COLOR_BGRA2BGR)
            with self._lock:
                self._frame = frame

    def get(self):
        with self._lock:
            return self._frame

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1.0)


# Define and parse user input arguments
parser = argparse.ArgumentParser()
parser.add_argument('--model', help='Path to YOLO model file (example: "runs/detect/train/weights/best.pt")',
                    required=True)
parser.add_argument('--source', help='Image source, can be image file ("test.jpg"), \
                    image folder ("test_dir"), video file ("testvid.mp4"), or index of USB camera ("usb0")', 
                    required=True)
parser.add_argument('--thresh', help='Minimum confidence threshold for displaying detected objects (example: "0.4")',
                    default=0.5)
parser.add_argument('--resolution', help='Resolution in WxH to display inference results at (example: "640x480"), \
                    otherwise, match source resolution',
                    default=None)
parser.add_argument('--record', help='Record results from video or webcam and save it as "demo1.avi". Must specify --resolution argument to record.',
                    action='store_true')
parser.add_argument('--skip', help='Run YOLO inference every N frames (default: 1 = every frame). '
                    'Display still updates every frame; only inference is skipped.',
                    default=1, type=int)

args = parser.parse_args()

# Parse user inputs
model_path = args.model
img_source = args.source
min_thresh = float(args.thresh)
user_res = args.resolution
record = args.record
frame_skip = max(1, args.skip)

# Check if model file exists and is valid
if not os.path.exists(model_path):
    print('ERROR: Model path is invalid or model was not found. Make sure the model filename was entered correctly.')
    sys.exit(0)

# Load the model into memory and get labelmap
model = YOLO(model_path, task='detect')
labels = model.names

# Parse input to determine if image source is a file, folder, video, or USB camera
img_ext_list = ['.jpg','.JPG','.jpeg','.JPEG','.png','.PNG','.bmp','.BMP']
vid_ext_list = ['.avi','.mov','.mp4','.mkv','.wmv']

if os.path.isdir(img_source):
    source_type = 'folder'
elif os.path.isfile(img_source):
    _, ext = os.path.splitext(img_source)
    if ext in img_ext_list:
        source_type = 'image'
    elif ext in vid_ext_list:
        source_type = 'video'
    else:
        print(f'File extension {ext} is not supported.')
        sys.exit(0)
elif 'usb' in img_source:
    source_type = 'usb'
    usb_idx = int(img_source[3:])
elif 'picamera' in img_source:
    source_type = 'picamera'
    picam_idx = int(img_source[8:])
else:
    print(f'Input {img_source} is invalid. Please try again.')
    sys.exit(0)

# Parse user-specified display resolution
resize = False
if user_res:
    resize = True
    resW, resH = int(user_res.split('x')[0]), int(user_res.split('x')[1])

# Check if recording is valid and set up recording
if record:
    if source_type not in ['video', 'usb']:
        print('Recording only works for video and camera sources. Please try again.')
        sys.exit(0)
    if not user_res:
        print('Please specify resolution to record video at.')
        sys.exit(0)
    record_name = 'demo1.avi'
    record_fps = 30
    recorder = cv2.VideoWriter(record_name, cv2.VideoWriter_fourcc(*'MJPG'), record_fps, (resW, resH))

# ===== Initialize config + FSM =====
from pathlib import Path

cfg_path = Path(__file__).parent / "config" / "config.example.json"
cfg = load_config(cfg_path)
act = Actuator()
act.open()

fsm = FeederFSM(
    actuator=act,
    retract_delay_ms=int(cfg.actuator.retract_delay_ms),
    cooldown_s=2.0,
    motion_threshold=float(cfg.vision.motion_threshold),
    feeding_distance_m=float(cfg.actuator.feeding_distance_m),
    detection_distance_m=float(cfg.radar.detection_distance_m),
)

feeder_enabled = True

# Load or initialize image source
if source_type == 'image':
    imgs_list = [img_source]
elif source_type == 'folder':
    imgs_list = []
    filelist = glob.glob(img_source + '/*')
    for file in filelist:
        _, file_ext = os.path.splitext(file)
        if file_ext in img_ext_list:
            imgs_list.append(file)
elif source_type in ('video', 'usb'):
    cap_arg = img_source if source_type == 'video' else usb_idx
    cap = cv2.VideoCapture(cap_arg)
    if user_res:
        cap.set(3, resW)
        cap.set(4, resH)

elif source_type == 'picamera':
    from picamera2 import Picamera2

    cap = Picamera2()

    if user_res:
        cap.configure(
            cap.create_video_configuration(
                main={"format": "XRGB8888", "size": (resW, resH)}
            )
        )
    else:
        resW, resH = 640, 480
        cap.configure(
            cap.create_video_configuration(
                main={"format": "XRGB8888", "size": (resW, resH)}
            )
        )

    cap.start()
    # Start the threaded grabber — this is the key framerate improvement
    grabber = PiCameraGrabber(cap)
    print(f'[CAMERA] Threaded PiCamera grabber started at {resW}x{resH}')

# Bounding box colors (Tableau 10)
bbox_colors = [(164,120,87), (68,148,228), (93,97,209), (178,182,133), (88,159,106), 
               (96,202,231), (159,124,168), (169,162,241), (98,118,150), (172,176,184)]

# Initialize control and status variables
avg_frame_rate = 0
frame_rate_buffer = []
fps_avg_len = 200
img_count = 0
selected_class_ids = [21]
detection_count = 0

# Frame skip counter — tracks which frames get full inference
frame_index = 0

# Cache last inference results so skipped frames still show boxes
last_detections = []     # list of (xmin, ymin, xmax, ymax, classidx, conf)
last_object_count = 0

# Staleness: clear cached boxes if no detection for this many seconds.
# Prevents ghost boxes from lingering after the bear leaves the frame.
last_detection_time = 0.0
DETECTION_STALE_S = 0.5

# In-memory vision tracker for motion calculation
vision_tracker = VisionTracker()


def _send_vision_to_fsm(det, motion_magnitude):
    """Bridge from YOLO to FSM."""
    is_threat = motion_magnitude >= float(cfg.vision.motion_threshold)
    fsm.tick(
        enable=feeder_enabled,
        threat=is_threat,
        motion_magnitude=motion_magnitude,
        radar_distance_m=None,
        now=time.monotonic(),
    )
    print(
        f"[VISION] id={det.detection_id} motion={motion_magnitude:.2f} threat={is_threat} "
        f"fsm_state={fsm.state.name if hasattr(fsm.state, 'name') else fsm.state}"
    )


# ===== Main inference loop =====
while True:

    t_start = time.perf_counter()

    # --- Grab frame from source ---
    if source_type in ('image', 'folder'):
        if img_count >= len(imgs_list):
            print('All images have been processed. Exiting program.')
            sys.exit(0)
        frame = cv2.imread(imgs_list[img_count])
        img_count += 1

    elif source_type == 'video':
        ret, frame = cap.read()
        if not ret:
            print('Reached end of the video file. Exiting program.')
            break

    elif source_type == 'usb':
        ret, frame = cap.read()
        if frame is None or not ret:
            print('Unable to read frames from the camera. Exiting program.')
            break

    elif source_type == 'picamera':
        # Non-blocking: always get the latest frame the grabber thread captured
        frame = grabber.get()
        if frame is None:
            time.sleep(0.005)
            continue

    # Resize to display resolution if requested
    if resize:
        frame = cv2.resize(frame, (resW, resH))

    # --- Inference (every frame_skip frames) ---
    run_inference = (frame_index % frame_skip == 0)
    frame_index += 1

    if run_inference:
        # No imgsz override — let YOLOv8 use its native 640 for best accuracy.
        # Passing imgsz=320 on a 320x240 source was double-downscaling and
        # hurting detection quality with no real speed benefit at this resolution.
        results = model(frame, classes=selected_class_ids, verbose=False)
        detections = results[0].boxes

        last_detections = []
        last_object_count = 0

        for i in range(len(detections)):
            detection_count += 1

            xyxy_tensor = detections[i].xyxy.cpu()
            xyxy = xyxy_tensor.numpy().squeeze()
            xmin, ymin, xmax, ymax = xyxy.astype(int)

            classidx = int(detections[i].cls.item())
            conf = detections[i].conf.item()

            # Cache for skipped frames
            last_detections.append((xmin, ymin, xmax, ymax, classidx, conf))

            if conf > min_thresh:
                last_object_count += 1

            # Build detection block and send to FSM (only on inference frames)
            yolo_block = (
                f"Detection number: {detection_count}\n"
                f"Time: {time.perf_counter()}\n"
                f"Xmin = {xmin}\n"
                f"Xmax = {xmax}\n"
                f"Ymin = {ymin}\n"
                f"Ymax = {ymax}\n"
            )
            det = vision_tracker.parse_yolo_output(yolo_block)
            if det is not None:
                motion = vision_tracker.compute_motion(det)
                _send_vision_to_fsm(det, motion)

        if last_detections:
            # At least one detection — update freshness timestamp
            last_detection_time = time.perf_counter()
        else:
            # No bear found — tell tracker to count this as a lost frame
            vision_tracker.mark_no_detection()

        # Status line prints every inference frame so you can confirm
        # the model is running even when the bear isn't visible/moving
        print(
            f"[FRAME {frame_index}] objects={last_object_count} "
            f"fsm={fsm.state.name} "
            f"motion={vision_tracker._last_motion:.1f} "
            f"fps={avg_frame_rate:.1f}"
        )

    # --- Draw cached detections on every frame ---
    # Only draw if the cached boxes are still fresh (within DETECTION_STALE_S).
    # This prevents ghost boxes from lingering after the bear leaves the frame.
    boxes_are_fresh = (time.perf_counter() - last_detection_time) < DETECTION_STALE_S
    object_count = 0
    if boxes_are_fresh:
        for (xmin, ymin, xmax, ymax, classidx, conf) in last_detections:
            if conf > min_thresh:
                classname = labels[classidx]
                color = bbox_colors[classidx % 10]
                cv2.rectangle(frame, (xmin, ymin), (xmax, ymax), color, 2)
                label = f'{classname}: {int(conf*100)}%'
                labelSize, baseLine = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                label_ymin = max(ymin, labelSize[1] + 10)
                cv2.rectangle(frame, (xmin, label_ymin-labelSize[1]-10),
                              (xmin+labelSize[0], label_ymin+baseLine-10), color, cv2.FILLED)
                cv2.putText(frame, label, (xmin, label_ymin-7),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
                object_count += 1

    # --- Draw HUD ---
    if source_type in ('video', 'usb', 'picamera'):
        cv2.putText(frame, f'FPS: {avg_frame_rate:0.2f}', (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
    cv2.putText(frame, f'Objects: {object_count}', (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
    cv2.putText(frame, f'FSM: {fsm.state.name}', (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
    cv2.imshow('YOLO detection results', frame)
    if record:
        recorder.write(frame)

    # Key handling
    if source_type in ('image', 'folder'):
        key = cv2.waitKey()
    else:
        key = cv2.waitKey(1)

    if key == ord('q') or key == ord('Q'):
        break
    elif key == ord('s') or key == ord('S'):
        cv2.waitKey()
    elif key == ord('p') or key == ord('P'):
        cv2.imwrite('capture.png', frame)

    # FPS calculation
    t_stop = time.perf_counter()
    frame_rate_calc = float(1 / (t_stop - t_start))
    if len(frame_rate_buffer) >= fps_avg_len:
        frame_rate_buffer.pop(0)
    frame_rate_buffer.append(frame_rate_calc)
    avg_frame_rate = np.mean(frame_rate_buffer)


# ===== Clean up =====
print(f'Average pipeline FPS: {avg_frame_rate:.2f}')
if source_type in ('video', 'usb'):
    cap.release()
elif source_type == 'picamera':
    grabber.stop()
    cap.stop()
if record:
    recorder.release()
cv2.destroyAllWindows()
