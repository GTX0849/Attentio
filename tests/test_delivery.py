"""Spool and retry, tested with a fake sender."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared.schema import AttentionRecord
from worker.delivery import Delivery, SendError, Spool


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[list[AttentionRecord]] = []
        self.failure: SendError | None = None

    def send(self, records: list[AttentionRecord]) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append(list(records))

    @property
    def sent_starts(self) -> list[int]:
        return [int(r.window_start.timestamp()) for batch in self.sent for r in batch]


def rec(offset: int) -> AttentionRecord:
    return AttentionRecord(
        camera_id="CAM1",
        student_id="STU001",
        window_start=datetime.fromtimestamp(1_700_000_010 + offset, tz=UTC),
        attentive=30.0,
    )


@pytest.fixture
def spool_path(tmp_path: Path) -> Path:
    return tmp_path / "unsent.jsonl"


def test_successful_send_leaves_nothing_spooled(spool_path: Path) -> None:
    sender = FakeSender()
    delivery = Delivery(sender, Spool(spool_path))
    delivery.deliver([rec(0)])
    assert sender.sent_starts == [1_700_000_010]
    assert Spool(spool_path).load() == []


def test_failed_send_is_spooled_and_resent_with_the_next_window(spool_path: Path) -> None:
    sender = FakeSender()
    delivery = Delivery(sender, Spool(spool_path))

    sender.failure = SendError("backend down")
    delivery.deliver([rec(0)])
    delivery.deliver([rec(30)])
    assert sender.sent == []
    assert len(Spool(spool_path).load()) == 2

    sender.failure = None
    delivery.deliver([rec(60)])
    assert sender.sent_starts == [1_700_000_010, 1_700_000_040, 1_700_000_070]  # in order
    assert Spool(spool_path).load() == []


def test_spooled_records_are_retried_at_startup_by_a_new_process(spool_path: Path) -> None:
    down = FakeSender()
    down.failure = SendError("backend down")
    Delivery(down, Spool(spool_path)).deliver([rec(0)])

    up = FakeSender()
    Delivery(up, Spool(spool_path)).retry_pending()  # what the worker does at start-up
    assert up.sent_starts == [1_700_000_010]
    assert Spool(spool_path).load() == []


def test_retry_with_nothing_spooled_sends_nothing(spool_path: Path) -> None:
    sender = FakeSender()
    Delivery(sender, Spool(spool_path)).retry_pending()
    assert sender.sent == []


def test_permanently_rejected_records_are_dropped_not_retried_forever(spool_path: Path) -> None:
    sender = FakeSender()
    sender.failure = SendError("unknown student", permanent=True)
    delivery = Delivery(sender, Spool(spool_path))
    delivery.deliver([rec(0)])
    assert Spool(spool_path).load() == []
