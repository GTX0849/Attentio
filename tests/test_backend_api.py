"""Backend HTTP API against the PostgreSQL database at TEST_DATABASE_URL."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient


def window(minutes_ago: float, **seconds: float) -> dict[str, Any]:
    """A record whose window started `minutes_ago` minutes ago (aligned to 30 s)."""
    now = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    start = now.replace(second=(now.second // 30) * 30, microsecond=0)
    return {
        "camera_id": "CAM1",
        "student_id": "STU001",
        "window_start": start.isoformat(),
        **seconds,
    }


def test_batch_ingest_stores_every_record_and_returns_counts(client: TestClient) -> None:
    batch = [
        window(2, attentive=30),
        window(1.5, attentive=20, not_visible=10),
        window(1, neutral=30),
    ]
    resp = client.post("/api/records", json=batch)
    assert resp.status_code == 200
    assert resp.json() == {"received": 3, "inserted": 3, "updated": 0}

    live = client.get("/api/students/STU001/live", params={"minutes": 10}).json()
    assert len(live["windows"]) == 3


def test_repeating_a_window_replaces_the_stored_row(client: TestClient) -> None:
    first = window(1, attentive=10, distracted=20)
    assert client.post("/api/records", json=[first]).json()["inserted"] == 1

    second = {**first, "attentive": 30, "distracted": 0}
    resp = client.post("/api/records", json=[second])
    assert resp.json() == {"received": 1, "inserted": 0, "updated": 1}

    windows = client.get("/api/students/STU001/live").json()["windows"]
    assert len(windows) == 1
    assert windows[0]["attentive"] == 30
    assert windows[0]["distracted"] == 0


def test_live_reports_attention_and_presence_per_window(client: TestClient) -> None:
    client.post(
        "/api/records",
        json=[
            window(2, attentive=15, neutral=5, distracted=5, not_visible=5),
            window(1.5, not_visible=30),
            window(1, neutral=30),
        ],
    )
    w = client.get("/api/students/STU001/live", params={"minutes": 10}).json()["windows"]
    # attention = 15 / (15 + 5) ; presence = (15 + 5 + 5) / 30
    assert w[0]["attention"] == pytest.approx(0.75)
    assert w[0]["presence"] == pytest.approx(25 / 30)
    # Not visible all window: no attention figure, zero presence.
    assert w[1]["attention"] is None
    assert w[1]["presence"] == 0
    # Only neutral time: attention undefined (denominator 0), fully present.
    assert w[2]["attention"] is None
    assert w[2]["presence"] == 1


def test_live_only_returns_windows_inside_the_requested_minutes(client: TestClient) -> None:
    client.post("/api/records", json=[window(30, attentive=30), window(1, attentive=30)])
    windows = client.get("/api/students/STU001/live", params={"minutes": 5}).json()["windows"]
    assert len(windows) == 1


def test_live_windows_are_returned_oldest_first(client: TestClient) -> None:
    client.post("/api/records", json=[window(1, attentive=30), window(3, attentive=30)])
    starts = [w["window_start"] for w in client.get("/api/students/STU001/live").json()["windows"]]
    assert starts == sorted(starts)


def test_live_for_unknown_student_is_404_with_a_message(client: TestClient) -> None:
    resp = client.get("/api/students/NOPE/live")
    assert resp.status_code == 404
    assert "NOPE" in resp.json()["detail"]


def test_ingest_for_unknown_student_or_camera_is_rejected_and_stores_nothing(
    client: TestClient,
) -> None:
    good = window(1, attentive=30)
    bad_student = {**window(2, attentive=30), "student_id": "GHOST"}
    resp = client.post("/api/records", json=[good, bad_student])
    assert resp.status_code == 404
    assert "GHOST" in resp.json()["detail"]

    bad_camera = {**window(2, attentive=30), "camera_id": "CAM99"}
    resp = client.post("/api/records", json=[bad_camera])
    assert resp.status_code == 404
    assert "CAM99" in resp.json()["detail"]

    assert client.get("/api/students/STU001/live").json()["windows"] == []


def test_ingest_with_a_naive_timestamp_is_rejected(client: TestClient) -> None:
    rec = {**window(1, attentive=30), "window_start": "2026-01-01T10:00:00"}
    assert client.post("/api/records", json=[rec]).status_code == 422
