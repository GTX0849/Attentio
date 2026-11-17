"""The capture loop. Kept small: all decisions live in testable modules."""

import logging
import time
from pathlib import Path

import cv2

from shared.config import PROJECT_ROOT, Config
from worker.client import HttpSender
from worker.delivery import Delivery, Spool
from worker.detector import HaarReadingSource
from worker.perception import PerceptionSettings, PerceptionSource
from worker.preview import draw_preview
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
    detector: str = "mediapipe",
) -> int:
    delivery = Delivery(HttpSender(config.backend_url), Spool(spool_path))
    delivery.retry_pending()  # send anything left over from a previous run

    source: ReadingSource
    recalibrate_key = ""
    if detector == "haar":
        source = HaarReadingSource()  # fallback: a face or no face, nothing else
    else:
        from worker.mediapipe_source import MediaPipeReadingSource  # heavy import; only when used

        settings = PerceptionSettings.from_config(config.raw)
        try:
            source = MediaPipeReadingSource(settings)
        except FileNotFoundError as e:
            log.error("%s", e)
            return 1
        recalibrate_key = settings.recalibrate_key
        log.info(
            "Calibrating for %.0f s: look at the screen. Press '%s' in the preview window to calibrate again.",
            settings.calibration_seconds,
            recalibrate_key,
        )

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        log.error("Could not open the webcam (index %d)", camera_index)
        return 1

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
            log.debug(
                "reading face=%s shoulders=%s pitch=%+.1f yaw=%+.1f eyes_closed=%s state=%s",
                reading.face_present,
                reading.shoulders_present,
                reading.pitch_deg,
                reading.yaw_deg,
                reading.eyes_closed,
                tracker.current_state(now),
            )

            if preview and ok:
                calibration_left = (
                    source.calibration_remaining(now)
                    if isinstance(source, PerceptionSource) and source.calibrating
                    else None
                )
                shown = draw_preview(
                    frame,
                    reading,
                    tracker.current_state(now),
                    tracker.look_down_remaining_s(now),
                    calibration_left,
                )
                cv2.imshow("ClassPulse worker (q to quit)", shown)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if recalibrate_key and key == ord(recalibrate_key) and isinstance(source, PerceptionSource):
                    source.recalibrate()
                    log.info("Recalibrating: look at the screen.")

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
        close_source = getattr(source, "close", None)  # only the MediaPipe source holds resources
        if callable(close_source):
            close_source()
        if preview:
            cv2.destroyAllWindows()
        delivery.deliver(tracker.flush(time.time()))  # the partial window
        log.info("Stopped.")
    return 0
