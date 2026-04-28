"""
Polar Bear Feeder — Demo Mode
Run with: python demo.py
Auto-selects PiCamera, loads NCNN model, displays live YOLO overlay.
No FSM, no BLE, no actuator. Pure zookeeper show-and-tell.
Press Q to quit.
"""
import time
import threading
import sys

import cv2
import numpy as np
from picamera2 import Picamera2
from ultralytics import YOLO

MODEL_PATH = "yolo26n_ncnn_model"
BEAR_CLASS = 21
CONF_THRESH = 0.4
FRAME_SKIP = 2
RES_W, RES_H = 640, 480
DETECTION_STALE_S = 0.5


class PiCameraGrabber:
    def __init__(self):
        self.cap = Picamera2()
        self.cap.configure(
            self.cap.create_video_configuration(
                main={"format": "XRGB8888", "size": (RES_W, RES_H)}
            )
        )
        self.cap.start()
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[CAMERA] Started at {RES_W}x{RES_H}", flush=True)

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
        self.cap.stop()
        self.cap.close()


def main():
    print(f"[DEMO] Loading model: {MODEL_PATH}", flush=True)
    model = YOLO(MODEL_PATH, task="detect")
    labels = model.names
    print("[DEMO] Model loaded. Starting camera...", flush=True)

    grabber = PiCameraGrabber()

    # Give the grabber a moment to fill the first frame
    for _ in range(20):
        if grabber.get() is not None:
            break
        time.sleep(0.05)

    frame_index = 0
    last_detections = []
    last_detection_time = 0.0
    fps_buffer = []
    avg_fps = 0.0

    print("[DEMO] Running. Press Q in the display window to quit.", flush=True)
    cv2.namedWindow("Polar Bear Feeder — Demo", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Polar Bear Feeder — Demo", RES_W, RES_H)
    while True:
        t_start = time.perf_counter()

        frame = grabber.get()
        if frame is None:
            time.sleep(0.005)
            continue

        frame_index += 1
        run_inference = (frame_index % FRAME_SKIP == 0)

        if run_inference:
            results = model(frame, classes=[BEAR_CLASS], conf = 0.3, verbose=False)
            detections = results[0].boxes
            last_detections = []

            for det in detections:
                conf = det.conf.item()
                if conf < CONF_THRESH:
                    continue
                xyxy = det.xyxy.cpu().numpy().squeeze()
                xmin, ymin, xmax, ymax = xyxy.astype(int)
                classidx = int(det.cls.item())
                last_detections.append((xmin, ymin, xmax, ymax, classidx, conf))

            if last_detections:
                last_detection_time = time.perf_counter()

        # Draw boxes if still fresh
        display = frame.copy()
        boxes_fresh = (time.perf_counter() - last_detection_time) < DETECTION_STALE_S
        bear_count = 0

        if boxes_fresh:
            for (xmin, ymin, xmax, ymax, classidx, conf) in last_detections:
                bear_count += 1
                color = (0, 200, 80)
                cv2.rectangle(display, (xmin, ymin), (xmax, ymax), color, 2)
                label = f"{labels[classidx]}: {int(conf * 100)}%"
                label_sz, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
                ly = max(ymin, label_sz[1] + 10)
                cv2.rectangle(display,
                              (xmin, ly - label_sz[1] - 10),
                              (xmin + label_sz[0], ly + baseline - 10),
                              color, cv2.FILLED)
                cv2.putText(display, label, (xmin, ly - 7),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)

        # HUD
        cv2.putText(display, f"FPS: {avg_fps:.1f}",    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(display, f"Bears: {bear_count}",   (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(display, "DEMO MODE",               (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 100, 255), 2)

        cv2.imshow("Polar Bear Feeder — Demo", display)
        
        if cv2.waitKey(1) & 0xFF in (ord('q'), ord('Q')):
            break

        # FPS
        t_stop = time.perf_counter()
        fps_buffer.append(1.0 / max(t_stop - t_start, 1e-6))
        if len(fps_buffer) > 60:
            fps_buffer.pop(0)
        avg_fps = float(np.mean(fps_buffer))

    grabber.stop()
    cv2.destroyAllWindows()
    print(f"[DEMO] Exited. Avg FPS: {avg_fps:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
