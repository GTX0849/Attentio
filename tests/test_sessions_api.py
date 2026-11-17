"""Manual sessions and how they override the timetable, through the backend HTTP API."""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import FakeClock
from tests.test_lectures_api import MONDAY_PHYSICS, lectures, live, rec
from tests.test_timetable_api import HEADER, upload


def ids(client: TestClient, kind: str) -> dict[str, int]:
    return {item["name"]: item["id"] for item in client.get(f"/api/{kind}").json()}


def start_session(client: TestClient, room: str = "R101") -> dict[str, Any]:
    """Start a session in `room` for Dr Rao teaching Mathematics to CSE-A."""
    resp = client.post(
        "/api/sessions",
        json={
            "room_id": room,
            "teacher_id": ids(client, "teachers")["Dr Rao"],
            "subject_id": ids(client, "subjects")["Mathematics"],
            "section_id": ids(client, "sections")["CSE-A"],
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def setup(client: TestClient, clock: FakeClock) -> None:
    """A Monday 10:00 Physics slot with Dr Iyer, and the time set to 10:02 local (04:32 UTC)."""
    upload(
        client,
        MONDAY_PHYSICS + "Tue,10:00,10:50,R101,CSE-A,Mathematics,Dr Rao\n",
    )
    clock.set(datetime(2026, 3, 2, 4, 32, tzinfo=UTC))


def test_an_open_session_wins_over_the_timetable_and_the_timetable_returns_after_stop(
    client: TestClient, clock: FakeClock
) -> None:
    setup(client, clock)
    session = start_session(client)
    assert session["open"] is True
    assert session["source"] == "manual"
    assert session["end"] is None
    assert session["start"] == "2026-03-02T04:32:00Z"

    client.post("/api/records", json=[rec("2026-03-02T04:35:00")])  # inside the Physics slot
    clock.set(datetime(2026, 3, 2, 4, 40, tzinfo=UTC))
    stopped = client.post(f"/api/sessions/{session['id']}/stop").json()
    assert stopped["open"] is False
    assert stopped["end"] == "2026-03-02T04:40:00Z"

    # Still inside the Physics slot, now with no session open.
    client.post("/api/records", json=[rec("2026-03-02T04:41:00")])

    windows = live(client)
    assert [(w["lecture"]["source"], w["lecture"]["subject"]) for w in windows] == [
        ("manual", "Mathematics"),
        ("timetable", "Physics"),
    ]
    assert windows[0]["lecture"]["teacher"] == "Dr Rao"
    assert windows[0]["lecture"]["section"] == "CSE-A"
    assert windows[0]["lecture"]["id"] == session["id"]


def test_a_session_with_no_timetable_still_gives_records_a_lecture(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, HEADER + "Tue,10:00,10:50,R101,CSE-A,Mathematics,Dr Rao\n")
    clock.set(datetime(2026, 3, 2, 4, 32, tzinfo=UTC))
    session = start_session(client)
    client.post("/api/records", json=[rec("2026-03-02T04:35:00"), rec("2026-03-02T04:35:30")])
    assert {w["lecture"]["id"] for w in live(client)} == {session["id"]}
    assert len(lectures(client)) == 1


def test_a_window_that_is_mostly_inside_the_session_belongs_to_it(
    client: TestClient, clock: FakeClock
) -> None:
    setup(client, clock)
    clock.set(datetime(2026, 3, 2, 4, 35, 20, tzinfo=UTC))
    session = start_session(client)  # starts at 04:35:20
    # The 04:35:00 window runs to 04:35:30, so its middle (04:35:15) is before the start.
    client.post("/api/records", json=[rec("2026-03-02T04:35:00"), rec("2026-03-02T04:35:30")])
    sources = [(w["lecture"]["source"]) for w in live(client)]
    assert sources == ["timetable", "manual"]
    assert live(client)[1]["lecture"]["id"] == session["id"]


def test_a_record_for_a_window_inside_a_stopped_session_still_belongs_to_it(
    client: TestClient, clock: FakeClock
) -> None:
    setup(client, clock)
    session = start_session(client)
    clock.set(datetime(2026, 3, 2, 4, 40, tzinfo=UTC))
    client.post(f"/api/sessions/{session['id']}/stop")
    # A spooled window that was recorded during the session and arrives after the stop.
    client.post("/api/records", json=[rec("2026-03-02T04:36:00")])
    assert live(client)[0]["lecture"]["id"] == session["id"]


def test_only_one_session_can_be_open_per_room(client: TestClient, clock: FakeClock) -> None:
    setup(client, clock)
    first = start_session(client)
    again = client.post(
        "/api/sessions",
        json={
            "room_id": "R101",
            "teacher_id": ids(client, "teachers")["Dr Iyer"],
            "subject_id": ids(client, "subjects")["Physics"],
        },
    )
    assert again.status_code == 409
    assert "R101" in again.json()["detail"]

    # Another room is free, and R101 is free again after the stop.
    client.put("/api/cameras/CAM2", json={"room_id": "R202"})
    assert start_session(client, "R202")["room_id"] == "R202"
    client.post(f"/api/sessions/{first['id']}/stop")
    assert start_session(client)["id"] != first["id"]


def test_a_session_only_affects_cameras_in_its_room(client: TestClient, clock: FakeClock) -> None:
    setup(client, clock)
    client.put("/api/cameras/CAM2", json={"room_id": "R202"})
    session = start_session(client, "R202")
    client.post(
        "/api/records",
        json=[rec("2026-03-02T04:35:00"), {**rec("2026-03-02T04:36:00"), "camera_id": "CAM2"}],
    )
    by_start = {w["window_start"]: w["lecture"] for w in live(client)}
    assert by_start["2026-03-02T04:35:00Z"]["source"] == "timetable"  # CAM1 in R101
    assert by_start["2026-03-02T04:36:00Z"]["id"] == session["id"]  # CAM2 in R202


def test_sessions_can_be_listed_open_or_all(client: TestClient, clock: FakeClock) -> None:
    setup(client, clock)
    client.put("/api/cameras/CAM2", json={"room_id": "R202"})
    done = start_session(client)
    client.post(f"/api/sessions/{done['id']}/stop")
    running = start_session(client, "R202")

    assert [s["id"] for s in client.get("/api/sessions", params={"open": True}).json()] == [
        running["id"]
    ]
    assert {s["id"] for s in client.get("/api/sessions").json()} == {done["id"], running["id"]}
    assert [s["id"] for s in client.get("/api/sessions", params={"open": False}).json()] == [
        done["id"]
    ]


def test_starting_and_stopping_report_problems_clearly(client: TestClient, clock: FakeClock) -> None:
    setup(client, clock)
    teacher = ids(client, "teachers")["Dr Iyer"]
    subject = ids(client, "subjects")["Physics"]
    ok = {"room_id": "R101", "teacher_id": teacher, "subject_id": subject}

    assert client.post("/api/sessions", json={**ok, "room_id": "R999"}).status_code == 404
    assert client.post("/api/sessions", json={**ok, "teacher_id": 9999}).status_code == 404
    assert client.post("/api/sessions", json={**ok, "subject_id": 9999}).status_code == 404
    assert client.post("/api/sessions", json={**ok, "section_id": 9999}).status_code == 404
    assert client.get("/api/sessions").json() == []

    session = client.post("/api/sessions", json=ok).json()  # a session needs no section
    assert session["section"] is None
    assert client.post(f"/api/sessions/{session['id']}/stop").status_code == 200
    assert client.post(f"/api/sessions/{session['id']}/stop").status_code == 409
    assert client.post("/api/sessions/9999/stop").status_code == 404


def test_lectures_list_shows_sessions_and_timetable_lectures_newest_first(
    client: TestClient, clock: FakeClock
) -> None:
    setup(client, clock)
    session = start_session(client)
    client.post("/api/records", json=[rec("2026-03-02T04:35:00")])
    clock.set(datetime(2026, 3, 2, 4, 50, tzinfo=UTC))
    client.post(f"/api/sessions/{session['id']}/stop")
    client.post("/api/records", json=[rec("2026-03-02T04:52:00")])

    found = lectures(client)
    # The session started at 04:32, after the 04:30 start of the Physics slot.
    assert [(x["source"], x["subject"]) for x in found] == [
        ("manual", "Mathematics"),
        ("timetable", "Physics"),
    ]
    assert found[0]["id"] == session["id"]
    limited = client.get("/api/lectures", params={"limit": 1}).json()
    assert len(limited) == 1
