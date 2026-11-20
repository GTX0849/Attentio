"""The worker: capture, scoring, display and sending run concurrently.

* Capture thread: reads the webcam as fast as it delivers and keeps only the newest
  frame, so the preview is live and never shows a stale buffered frame.
* Scoring thread: samples the newest frame at `worker.target_fps`, runs the reading
  source (MediaPipe) and the state rules, and publishes the current status.
* Main thread: shows every new frame with the latest status drawn on it and handles
  the q, recalibrate and m (landmark overlay) keys. OpenCV windows must live on the main thread.
* Delivery thread: sends finished windows in the background (see worker/concurrency.py),
  so a slow or unreachable backend never freezes the preview.

Tune mode (`--tune`, test sessions only; see worker/tuning.py): the display thread turns the
number keys into a label held in a `LabelHolder`, and the scoring thread saves each tick's
angles with that label through a `TuneRecorder`.

All decisions still live in testable modules; this file only wires them together.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2

from shared.config import PROJECT_ROOT, Config, service_key
from worker.client import NO_KEY_MESSAGE, HttpSender
from worker.concurrency import BackgroundDelivery, LabelHolder, LatestFrame, Status, StatusBoard
from worker.delivery import Spool
from worker.detector import HaarReadingSource
from worker.perception import Diagnostics, PerceptionSettings, PerceptionSource
from worker.preview import draw_preview
from worker.readings import ReadingSource
from worker.states import StateThresholds
from worker.tracker import AttentionTracker
from worker.tuning import (
    DEFAULT_TUNING_DIR,
    TuneRecorder,
    format_summary,
    handle_tune_key,
    on_tune_finished as default_on_tune_finished,
)

log = logging.getLogger("worker")

DEFAULT_SPOOL = PROJECT_ROOT / ".spool" / "unsent.jsonl"
WINDOW_TITLE = "ClassPulse worker (q to quit)"
LANDMARKS_KEY = "m"  # toggles the landmark overlay in the preview
STALE_FRAME_S = 1.0  # a frame older than this counts as no frame (camera stalled or unplugged)


def run(
    student: str,
    camera: str,
    config: Config,
    spool_path: Path = DEFAULT_SPOOL,
    preview: bool = True,
    camera_index: int = 0,
    detector: str = "mediapipe",
    tune: bool = False,
    tuning_dir: Path = DEFAULT_TUNING_DIR,
    on_tune_finished: Callable[[Path, str], None] = default_on_tune_finished,
) -> int:
    """Run until q or Ctrl+C. With `tune`, label keys and a tune file are added (test sessions only);
    `on_tune_finished(path, camera_id)` is called once that file is closed."""
    if tune and detector == "haar":
        log.error("--tune needs the mediapipe detector (the haar fallback has no head angles to save)")
        return 1
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
            "Calibrating for %.0f s: look at the teacher. Press '%s' in the preview window to calibrate again.",
            settings.calibration_seconds,
            recalibrate_key,
        )

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        log.error("Could not open the webcam (index %d)", camera_index)
        return 1
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # ask for no backlog; the capture thread drains it anyway

    key = service_key()
    if key is None:
        log.warning(NO_KEY_MESSAGE)
    delivery = BackgroundDelivery(HttpSender(config.backend_url, service_key=key), Spool(spool_path))
    tracker = AttentionTracker(camera, student, config.window_seconds, StateThresholds.from_config(config.raw))
    latest = LatestFrame()
    board = StatusBoard()
    stop = threading.Event()
    recalibrate = threading.Event()
    labels = LabelHolder() if tune else None
    recorder = TuneRecorder(camera, tuning_dir) if tune else None
    if recorder is not None:
        log.info("Tune mode: saving labelled angles to %s (test sessions only)", recorder.path)
        if not preview:
            log.warning("Tune mode without the preview has no keys: every row will carry label 0")

    threads = [
        threading.Thread(target=_capture_loop, args=(capture, latest, stop), name="capture", daemon=True),
        threading.Thread(
            target=_scoring_loop,
            args=(source, tracker, delivery, latest, board, stop, recalibrate, config.target_fps, recorder, labels),
            name="scoring",
            daemon=True,
        ),
    ]
    delivery.start()
    for t in threads:
        t.start()
    log.info("Running for %s on %s. Press q in the preview window or Ctrl+C to stop.", student, camera)

    try:
        if preview:
            _display_loop(latest, board, stop, recalibrate, recalibrate_key, labels)
        else:
            while not stop.is_set():
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
        capture.release()
        close_source = getattr(source, "close", None)  # only the MediaPipe source holds resources
        if callable(close_source):
            close_source()
        if preview:
            cv2.destroyAllWindows()
        delivery.close()  # sends the partial window the scoring thread flushed
        if recorder is not None:
            summary = recorder.close()
            print(format_summary(summary), flush=True)
            if summary.path is not None:
                on_tune_finished(summary.path, summary.camera_id)  # CLA-21 hooks its analysis in here
        log.info("Stopped.")
    return 0


def _capture_loop(capture: Any, latest: LatestFrame, stop: threading.Event) -> None:
    while not stop.is_set():
        ok, frame = capture.read()  # blocks until the camera's next frame
        if ok:
            latest.put(frame, time.time())
        else:
            time.sleep(0.05)  # camera hiccup: don't spin


def _scoring_loop(
    source: ReadingSource,
    tracker: AttentionTracker,
    delivery: BackgroundDelivery,
    latest: LatestFrame,
    board: StatusBoard,
    stop: threading.Event,
    recalibrate: threading.Event,
    target_fps: float,
    recorder: TuneRecorder | None = None,
    labels: LabelHolder | None = None,
) -> None:
    interval = 1.0 / target_fps
    next_tick = time.time()
    try:
        while not stop.is_set():
            if recalibrate.is_set() and isinstance(source, PerceptionSource):
                recalibrate.clear()
                source.recalibrate()
                tracker.reset_learning()  # the learned front region belongs to the old calibration
                log.info("Recalibrating: look at the teacher.")

            now = time.time()
            got = latest.get()
            frame = got.frame if got is not None and now - got.timestamp <= STALE_FRAME_S else None
            diagnostics: Diagnostics | None = None
            if isinstance(source, PerceptionSource):
                reading, diagnostics = source.read_with_diagnostics(frame, now)
            else:
                reading = source.read(frame, now)
            delivery.submit(tracker.observe(reading))
            calibration_left = (
                source.calibration_remaining(now)
                if isinstance(source, PerceptionSource) and source.calibrating
                else None
            )
            status = Status(
                reading,
                tracker.current_state(now),
                tracker.look_down_remaining_s(now),
                calibration_left,
                diagnostics=diagnostics,
                reference=tracker.reference(),
                region=tracker.extents(),
            )
            board.set(status)
            if recorder is not None:
                recorder.record(status, labels.get() if labels is not None else 0, now)
            log.debug(
                "reading faces=%d face=%s shoulders=%s pitch=%+.1f yaw=%+.1f eyes_closed=%s state=%s",
                diagnostics.face_count if diagnostics is not None else 0,
                reading.face_present,
                reading.shoulders_present,
                reading.pitch_deg,
                reading.yaw_deg,
                reading.eyes_closed,
                tracker.current_state(now),
            )

            next_tick += interval
            delay = next_tick - time.time()
            if delay > 0:
                stop.wait(delay)  # wakes at once on stop
            else:
                next_tick = time.time()  # running behind: don't try to catch up
    except Exception:
        log.exception("Scoring stopped on an error")
        stop.set()
    finally:
        delivery.submit(tracker.flush(time.time()))  # the partial window


def _display_loop(
    latest: LatestFrame,
    board: StatusBoard,
    stop: threading.Event,
    recalibrate: threading.Event,
    recalibrate_key: str,
    labels: LabelHolder | None = None,
) -> None:
    shown_sequence = 0
    show_landmarks = False  # toggled with m; always on while calibrating (see draw_preview)
    while not stop.is_set():
        got = latest.get()
        status = board.get()
        if got is not None and status is not None and got.sequence != shown_sequence:
            shown_sequence = got.sequence
            cv2.imshow(
                WINDOW_TITLE,
                draw_preview(
                    got.frame,
                    status.reading,
                    status.state,
                    status.look_down_left_s,
                    status.calibration_left_s,
                    diagnostics=status.diagnostics,
                    overlay=show_landmarks,
                    reference=status.reference,
                    region=status.region,
                    recalibrate_key=recalibrate_key or None,
                    landmarks_key=LANDMARKS_KEY,
                    tune_label=labels.get() if labels is not None else None,
                ),
            )
        key = cv2.waitKey(5) & 0xFF  # also pumps the window's events
        if key == ord("q"):
            stop.set()
        elif key == ord(LANDMARKS_KEY):
            show_landmarks = not show_landmarks
        elif recalibrate_key and key == ord(recalibrate_key):
            recalibrate.set()
        else:
            handle_tune_key(key, labels)  # the number keys, only in tune mode
