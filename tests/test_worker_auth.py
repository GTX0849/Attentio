"""The worker sends the service key, and a refusal for a missing key keeps the records."""

from datetime import UTC, datetime
from typing import Any

import pytest
import requests

from shared.schema import AttentionRecord
from worker.client import HttpSender
from worker.delivery import SendError

RECORD = AttentionRecord(
    camera_id="CAM1",
    student_id="STU001",
    window_start=datetime(2026, 3, 2, 10, 0, tzinfo=UTC),
    attentive=20,
    neutral=0,
    distracted=0,
    not_visible=10,
)


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "body"


def fake_post(monkeypatch: pytest.MonkeyPatch, status: int) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        seen.append({"url": url, **kwargs})
        return FakeResponse(status)

    monkeypatch.setattr(requests, "post", post)
    return seen


def test_the_key_goes_in_the_x_classpulse_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_post(monkeypatch, 200)
    HttpSender("http://backend:8000/", service_key="the-key").send([RECORD])
    assert seen[0]["url"] == "http://backend:8000/api/records"
    assert seen[0]["headers"] == {"X-ClassPulse-Key": "the-key"}


def test_without_a_key_no_header_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_post(monkeypatch, 200)
    HttpSender("http://backend:8000").send([RECORD])
    assert seen[0]["headers"] == {}


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_not_permanent_and_says_what_to_do(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    fake_post(monkeypatch, status)
    with pytest.raises(SendError) as missing:
        HttpSender("http://b").send([RECORD])
    assert not missing.value.permanent and "CLASSPULSE_SERVICE_KEY is not set" in str(missing.value)
    with pytest.raises(SendError) as wrong:
        HttpSender("http://b", service_key="k").send([RECORD])
    assert not wrong.value.permanent and "service key is wrong" in str(wrong.value)
    assert "k" != str(wrong.value)


def test_a_rejected_record_is_still_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post(monkeypatch, 404)
    with pytest.raises(SendError) as err:
        HttpSender("http://b", service_key="k").send([RECORD])
    assert err.value.permanent


def test_the_sender_waits_three_seconds_unless_the_timeout_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_post(monkeypatch, 200)
    monkeypatch.delenv("CLASSPULSE_HTTP_TIMEOUT", raising=False)
    HttpSender("http://b").send([RECORD])
    monkeypatch.setenv("CLASSPULSE_HTTP_TIMEOUT", "20")
    HttpSender("http://b").send([RECORD])
    HttpSender("http://b", timeout=1.5).send([RECORD])  # an explicit timeout always wins
    assert [call["timeout"] for call in seen] == [3.0, 20.0, 1.5]
