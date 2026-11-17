"""The capture loop. Kept small: all decisions live in testable modules."""

import logging
import time
from pathlib import Path

import cv2

from shared.config import PROJECT_ROOT, Config
from worker.client import HttpSender
from worker.delivery import Delivery, Spool
from worker.detector import HaarReadingSource
from worker.readings import ReadingSource
from worker.states import StateThresholds
from worker.tracker import AttentionTracker

log = logging.getLogger("worker")

DEFAULT_SPOOL = PROJECT_ROOT / ".spool" / "unsent.jsonl"


def run(
    student: str,
    camera: str,
    config: Config,
    spool_path: Path = DEFAULT_SPOOL,
    preview: bool = True,
    camera_index: int = 0,
) -> int:
    delivery = Delivery(HttpSender(config.backend_url), Spool(spool_path))
    delivery.retry_pending()  # send anything left over from a previous run

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        log.error("Could not open the webcam (index %d)", camera_index)
        return 1

    source: ReadingSource = HaarReadingSource()  # CLA-10 swaps in the real perception here
    tracker = AttentionTracker(
        camera, student, config.window_seconds, StateThresholds.from_config(config.raw)
    )
    frame_interval = 1.0 / config.target_fps
    log.info("Running for %s on %s. Press q in the preview window or Ctrl+C to stop.", student, camera)

    try:
        next_frame = time.time()
        while True:
            ok, frame = capture.read()
            now = time.time()
            reading = source.read(frame if ok else None, now)
            delivery.deliver(tracker.observe(reading))

            if preview and ok:
                label = "face" if reading.face_present else "no face"
                cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.imshow("ClassPulse worker (q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            next_frame += frame_interval
            delay = next_frame - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.time()  # running behind: don't try to catch up
    except KeyboardInterrupt:
        pass
    finally:
        capture.release()
        if preview:
            cv2.destroyAllWindows()
        delivery.deliver(tracker.flush(time.time()))  # the partial window
        log.info("Stopped.")
    return 0
