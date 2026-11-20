"""Thread-safe pieces that let capture, scoring, display and sending run concurrently.

The worker used to do all four in one loop, so the preview showed at most the scoring
rate and froze whenever a send was slow (on Windows a refused localhost connection takes
about two seconds). Now:

* the capture thread reads the camera as fast as it delivers and keeps only the newest
  frame in a `LatestFrame`, so nothing waits behind a stale buffer;
* the scoring thread samples that frame at the configured rate and publishes a `Status`;
* the display (main thread) shows every new frame with the latest `Status` drawn on it;
* `BackgroundDelivery` sends records on its own thread and retries on an interval.
"""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

from shared.schema import AttentionRecord, AttentionState
from worker.delivery import Delivery, Sender, Spool
from worker.perception import Diagnostics
from worker.readings import Reading

log = logging.getLogger("worker.concurrency")


@dataclass(frozen=True)
class Frame:
    frame: Any
    timestamp: float
    sequence: int


class LatestFrame:
    """Holds only the newest camera frame. The camera thread puts, everyone else reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._count = 0

    def put(self, frame: Any, timestamp: float) -> None:
        with self._lock:
            self._count += 1
            self._latest = Frame(frame, timestamp, self._count)

    def get(self) -> Frame | None:
        with self._lock:
            return self._latest


@dataclass(frozen=True)
class Status:
    """What the scoring thread last decided, for the preview to draw."""

    reading: Reading
    state: AttentionState | None
    look_down_left_s: float
    calibration_left_s: float | None
    diagnostics: Diagnostics | None = None  # faces and shoulders for the overlay; None without MediaPipe
    reference: tuple[float, float] | None = None  # learned front-region centre (pitch, yaw), degrees
    region: tuple[float, float] | None = None  # front-region extents (up, sideways), degrees


class StatusBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: Status | None = None

    def set(self, status: Status) -> None:
        with self._lock:
            self._status = status

    def get(self) -> Status | None:
        with self._lock:
            return self._status


class LabelHolder:
    """The tune-mode label (0 to 5), set by the display thread's keys and read by the scoring thread."""

    def __init__(self, label: int = 0) -> None:
        self._lock = threading.Lock()
        self._label = label

    def set(self, label: int) -> None:
        with self._lock:
            self._label = label

    def get(self) -> int:
        with self._lock:
            return self._label


_STOP = object()


class BackgroundDelivery:
    """Delivery on its own thread. `submit` never blocks the caller on the network.

    Records still go to the spool before any send (see `Delivery`), so an outage or a
    crash loses nothing. While something is pending, the spool is retried every
    `retry_interval_s` seconds rather than on every loop turn.
    """

    def __init__(self, sender: Sender, spool: Spool, retry_interval_s: float = 5.0) -> None:
        self._delivery = Delivery(sender, spool)
        self._spool = spool
        self._retry_interval_s = retry_interval_s
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="delivery", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, records: list[AttentionRecord]) -> None:
        if records:
            self._queue.put(records)

    def close(self, timeout_s: float = 5.0) -> None:
        """Send what is queued (one last attempt), then stop. Unsent records stay in the spool."""
        self._queue.put(_STOP)
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            log.warning("Delivery still busy after %.0f s; unsent records stay in the spool", timeout_s)

    def _run(self) -> None:
        self._guarded(self._delivery.retry_pending)  # anything left over from a previous run
        while True:
            try:
                item = self._queue.get(timeout=self._retry_interval_s)
            except queue.Empty:
                self._guarded(self._delivery.retry_pending)  # cheap when the spool is empty
                continue
            if item is _STOP:
                self._guarded(self._drain_and_send_last)
                return
            assert isinstance(item, list)
            records: list[AttentionRecord] = item
            self._guarded(lambda: self._delivery.deliver(records))

    @staticmethod
    def _guarded(action: Any) -> None:
        """Keep the delivery thread alive through an unexpected error (e.g. a spool disk error)."""
        try:
            action()
        except Exception:
            log.exception("Delivery step failed; will retry")

    def _drain_and_send_last(self) -> None:
        last: list[AttentionRecord] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, list):
                last.extend(item)
        self._delivery.deliver(last)  # spools `last`, then one send of everything pending
