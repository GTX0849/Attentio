"""The worker's concurrent parts: the latest-frame holder, the status board and background delivery.

These are what keep the preview live: capture, scoring, display and sending each run
on their own, so a slow or unreachable backend can never stall the camera window.
"""

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from shared.schema import AttentionRecord
from worker.concurrency import BackgroundDelivery, LatestFrame, Status, StatusBoard
from worker.delivery import SendError, Spool
from worker.readings import Reading


def record(minute: int) -> AttentionRecord:
    return AttentionRecord(
        camera_id="CAM1",
        student_id="STU001",
        window_start=datetime(2026, 11, 17, 10, minute, tzinfo=UTC),
        attentive=30.0,
    )


def wait_until(condition: object, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(condition) and condition():
            return True
        time.sleep(0.005)
    return False


class BlockingSender:
    """Blocks every send until released, like a backend that takes seconds to answer."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.sent: list[AttentionRecord] = []

    def send(self, records: list[AttentionRecord]) -> None:
        self.release.wait(5)
        self.sent.extend(records)


class FailingSender:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = True
        self.sent: list[AttentionRecord] = []

    def send(self, records: list[AttentionRecord]) -> None:
        self.calls += 1
        if self.fail:
            raise SendError("backend unreachable")
        self.sent.extend(records)


# ---- LatestFrame ----------------------------------------------------------------------------------------------


def test_latest_frame_is_empty_until_a_frame_arrives() -> None:
    assert LatestFrame().get() is None


def test_latest_frame_keeps_only_the_newest_frame_and_counts_them() -> None:
    latest = LatestFrame()
    latest.put("frame-1", 10.0)
    latest.put("frame-2", 10.1)
    got = latest.get()
    assert got is not None
    assert got.frame == "frame-2" and got.timestamp == 10.1 and got.sequence == 2


# ---- StatusBoard ---------------------------------------------------------------------------------------------


def test_status_board_hands_the_display_the_latest_status() -> None:
    board = StatusBoard()
    assert board.get() is None
    status = Status(Reading(timestamp=1.0, face_present=True, shoulders_present=True), None, 8.0, 2.5)
    board.set(status)
    assert board.get() == status


# ---- BackgroundDelivery --------------------------------------------------------------------------------------


def test_submit_returns_at_once_even_while_the_backend_is_slow(tmp_path: Path) -> None:
    sender = BlockingSender()
    delivery = BackgroundDelivery(sender, Spool(tmp_path / "spool.jsonl"), retry_interval_s=0.05)
    delivery.start()
    try:
        started = time.monotonic()
        delivery.submit([record(0)])
        delivery.submit([record(1)])
        assert time.monotonic() - started < 0.05  # the caller (the scoring loop) never waits for the network
        sender.release.set()
        assert wait_until(lambda: len(sender.sent) >= 2)
    finally:
        delivery.close(timeout_s=2)
    assert {r.window_start.minute for r in sender.sent} == {0, 1}


def test_a_down_backend_is_retried_on_an_interval_not_continuously(tmp_path: Path) -> None:
    sender = FailingSender()
    delivery = BackgroundDelivery(sender, Spool(tmp_path / "spool.jsonl"), retry_interval_s=0.1)
    delivery.start()
    try:
        delivery.submit([record(0)])
        time.sleep(0.35)
        assert 2 <= sender.calls <= 6  # roughly one attempt per interval, not one per loop turn
        sender.fail = False
        assert wait_until(lambda: len(sender.sent) == 1)
    finally:
        delivery.close(timeout_s=2)


def test_records_survive_on_disk_while_the_backend_is_down(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool.jsonl")
    sender = FailingSender()
    delivery = BackgroundDelivery(sender, spool, retry_interval_s=10)
    delivery.start()
    delivery.submit([record(0)])
    assert wait_until(lambda: sender.calls >= 1)
    delivery.close(timeout_s=0.5)
    assert [r.window_start.minute for r in spool.load()] == [0]


def test_close_sends_what_was_submitted_last(tmp_path: Path) -> None:
    sender = FailingSender()
    sender.fail = False
    delivery = BackgroundDelivery(sender, Spool(tmp_path / "spool.jsonl"), retry_interval_s=10)
    delivery.start()
    delivery.submit([record(5)])  # e.g. the partial window flushed on shutdown
    delivery.close(timeout_s=2)
    assert [r.window_start.minute for r in sender.sent] == [5]
