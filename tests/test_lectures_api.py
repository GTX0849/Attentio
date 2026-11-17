"""Lecture resolution on ingest, through the backend HTTP API."""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ClientFactory, FakeClock
from tests.test_timetable_api import HEADER, upload

# 2026-03-02 and 2026-03-09 are Mondays. 10:05 in Asia/Kolkata (UTC+5:30) is 04:35 UTC.
MONDAY_PHYSICS = HEADER + "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def rec(window_start: str, **seconds: float) -> dict[str, Any]:
    return {
        "camera_id": "CAM1",
        "student_id": "STU001",
        "window_start": f"{window_start}+00:00",
        "attentive": 20,
        "not_visible": 10,
        **seconds,
    }


def lectures(client: TestClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = client.get("/api/lectures").json()
    return out


def live(client: TestClient, minutes: int = 120) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = client.get(
        "/api/students/STU001/live", params={"minutes": minutes}
    ).json()["windows"]
    return out


def test_no_lecture_exists_until_a_record_needs_it(client: TestClient) -> None:
    upload(client, MONDAY_PHYSICS)
    assert lectures(client) == []


def test_the_first_record_in_a_slot_creates_the_lecture_and_later_records_reuse_it(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, MONDAY_PHYSICS)
    clock.set(utc("2026-03-02T04:45:00"))
    assert client.post("/api/records", json=[rec("2026-03-02T04:35:00")]).status_code == 200
    first = lectures(client)
    assert len(first) == 1
    assert first[0]["teacher"] == "Dr Iyer"
    assert first[0]["subject"] == "Physics"
    assert first[0]["section"] == "CSE-A"
    assert first[0]["room_id"] == "R101"
    assert first[0]["source"] == "timetable"
    assert first[0]["start"] == "2026-03-02T04:30:00Z"  # 10:00 Asia/Kolkata
    assert first[0]["end"] == "2026-03-02T05:20:00Z"  # 10:50 Asia/Kolkata

    client.post("/api/records", json=[rec("2026-03-02T04:35:30"), rec("2026-03-02T04:40:00")])
    again = lectures(client)
    assert [x["id"] for x in again] == [first[0]["id"]]

    windows = live(client)
    assert len(windows) == 3
    assert {w["lecture"]["id"] for w in windows} == {first[0]["id"]}
    assert windows[0]["lecture"]["subject"] == "Physics"


def test_the_same_slot_on_two_dates_gives_two_lectures(client: TestClient) -> None:
    upload(client, MONDAY_PHYSICS)
    client.post("/api/records", json=[rec("2026-03-02T04:35:00"), rec("2026-03-09T04:35:00")])
    found = lectures(client)  # newest first
    assert [x["start"] for x in found] == ["2026-03-09T04:30:00Z", "2026-03-02T04:30:00Z"]


def test_slots_are_matched_in_local_time_not_utc(client: TestClient) -> None:
    upload(client, MONDAY_PHYSICS)
    # 10:05 UTC is 15:35 in Asia/Kolkata: outside the 10:00 to 10:50 slot.
    client.post("/api/records", json=[rec("2026-03-02T10:05:00")])
    assert lectures(client) == []


def test_the_weekday_is_the_local_weekday(client: TestClient) -> None:
    # Sunday 20:00 UTC is Monday 01:30 in Asia/Kolkata.
    upload(client, HEADER + "Mon,01:00,02:00,R101,CSE-A,Night owls,Dr Iyer\n")
    client.post("/api/records", json=[rec("2026-03-01T20:00:00")])
    found = lectures(client)
    assert [x["subject"] for x in found] == ["Night owls"]
    assert found[0]["start"] == "2026-03-01T19:30:00Z"


def test_a_slot_covers_its_start_but_not_its_end(client: TestClient) -> None:
    upload(client, MONDAY_PHYSICS)
    client.post(
        "/api/records",
        json=[
            rec("2026-03-02T04:29:30"),  # 09:59:30 local, before the slot
            rec("2026-03-02T05:20:00"),  # 10:50:00 local, the slot has ended
            rec("2026-03-03T04:35:00"),  # Tuesday
        ],
    )
    assert lectures(client) == []
    client.post("/api/records", json=[rec("2026-03-02T04:30:00"), rec("2026-03-02T05:19:30")])
    assert len(lectures(client)) == 1


def test_a_camera_in_another_room_matches_that_rooms_slots(client: TestClient) -> None:
    upload(client, HEADER + "Mon,10:00,10:50,R202,CSE-B,Biology,Dr Rao\n")
    client.put("/api/cameras/CAM2", json={"room_id": "R202"})
    client.post("/api/records", json=[rec("2026-03-02T04:35:00")])  # CAM1 is in R101
    assert lectures(client) == []
    client.post("/api/records", json=[{**rec("2026-03-02T04:35:00"), "camera_id": "CAM2"}])
    assert [x["room_id"] for x in lectures(client)] == ["R202"]


def test_the_time_zone_is_configurable_and_follows_daylight_saving(
    make_client: ClientFactory,
) -> None:
    # New York moved from UTC-5 to UTC-4 on 2026-03-08, so 10:05 local is 15:05 UTC
    # on 2026-03-02 and 14:05 UTC on 2026-03-09.
    with make_client("America/New_York") as client:
        upload(client, MONDAY_PHYSICS)
        assert client.get("/api/timetable").json()["timezone"] == "America/New_York"
        client.post(
            "/api/records",
            json=[
                rec("2026-03-02T15:05:00"),
                rec("2026-03-09T14:05:00"),
                rec("2026-03-09T15:05:00"),  # 11:05 local, after the slot
            ],
        )
        found = lectures(client)
        assert [x["start"] for x in found] == ["2026-03-09T14:00:00Z", "2026-03-02T15:00:00Z"]


def test_a_record_that_matches_nothing_is_stored_without_a_lecture_and_still_shown_live(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, MONDAY_PHYSICS)
    clock.set(utc("2026-03-03T09:00:00"))  # Tuesday, no slot
    resp = client.post(
        "/api/records", json=[rec("2026-03-03T08:58:00", attentive=25, neutral=5, not_visible=0)]
    )
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 1

    windows = live(client)
    assert len(windows) == 1
    assert windows[0]["lecture"] is None
    assert windows[0]["attentive"] == 25
    assert lectures(client) == []


def test_a_re_sent_record_keeps_its_lecture_even_after_the_template_changes(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, MONDAY_PHYSICS)
    clock.set(utc("2026-03-02T04:45:00"))
    client.post("/api/records", json=[rec("2026-03-02T04:35:00", attentive=10)])
    original = live(client)[0]["lecture"]
    assert original["teacher"] == "Dr Iyer"

    # A different teacher takes over the slot, then the worker re-sends the window.
    upload(client, HEADER + "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Rao\n")
    resp = client.post(
        "/api/records", json=[rec("2026-03-02T04:35:00", attentive=28, not_visible=2)]
    )
    assert resp.json() == {"received": 1, "inserted": 0, "updated": 1}

    windows = live(client)
    assert len(windows) == 1
    assert windows[0]["attentive"] == 28
    assert windows[0]["lecture"]["id"] == original["id"]
    assert windows[0]["lecture"]["teacher"] == "Dr Iyer"
    assert len(lectures(client)) == 1  # the re-send did not create a second lecture


def test_lectures_already_created_keep_working_after_the_timetable_is_replaced(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, MONDAY_PHYSICS)
    clock.set(utc("2026-03-02T04:45:00"))
    client.post("/api/records", json=[rec("2026-03-02T04:35:00")])

    upload(client, HEADER + "Tue,10:00,10:50,R101,CSE-A,Biology,Dr Rao\n")  # Monday slot removed
    found = lectures(client)
    assert [(x["subject"], x["teacher"]) for x in found] == [("Physics", "Dr Iyer")]
    assert live(client)[0]["lecture"]["id"] == found[0]["id"]


def test_a_re_sent_unassigned_record_gains_a_lecture_once_the_timetable_exists(
    client: TestClient, clock: FakeClock
) -> None:
    clock.set(utc("2026-03-02T04:45:00"))
    client.post("/api/records", json=[rec("2026-03-02T04:35:00")])
    assert live(client)[0]["lecture"] is None

    upload(client, MONDAY_PHYSICS)
    client.post("/api/records", json=[rec("2026-03-02T04:35:00")])
    assert live(client)[0]["lecture"]["subject"] == "Physics"
