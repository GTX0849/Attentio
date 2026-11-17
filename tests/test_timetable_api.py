"""Timetable upload, listing and camera registration through the backend HTTP API."""

from typing import Any

from fastapi.testclient import TestClient

HEADER = "day,start,end,room,section,subject,teacher\n"

VALID = HEADER + (
    "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
    "Mon,11:00,11:50,R101,CSE-A,Mathematics,Dr Rao\n"
    "Tue,09:00,09:50,R202,CSE-B,Physics,Dr Iyer\n"
)


def upload(client: TestClient, csv_text: str) -> dict[str, Any]:
    resp = client.post(
        "/api/timetable", files={"file": ("timetable.csv", csv_text.encode(), "text/csv")}
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def test_valid_upload_creates_slots_and_the_entities_they_name(client: TestClient) -> None:
    result = upload(client, VALID)
    assert result["applied"] is True
    assert result["errors"] == []
    assert result["slots_total"] == 3
    # R101 already exists from seeding, R202 is new.
    assert result["created"] == {"teachers": 2, "subjects": 2, "rooms": 1, "sections": 2}

    slots = client.get("/api/timetable").json()["slots"]
    assert [
        (s["day"], s["start"], s["end"], s["room"], s["section"], s["subject"], s["teacher"])
        for s in slots
    ] == [
        ("Mon", "10:00", "10:50", "R101", "CSE-A", "Physics", "Dr Iyer"),
        ("Mon", "11:00", "11:50", "R101", "CSE-A", "Mathematics", "Dr Rao"),
        ("Tue", "09:00", "09:50", "R202", "CSE-B", "Physics", "Dr Iyer"),
    ]
    assert sorted(t["name"] for t in client.get("/api/teachers").json()) == ["Dr Iyer", "Dr Rao"]
    assert sorted(s["name"] for s in client.get("/api/subjects").json()) == [
        "Mathematics",
        "Physics",
    ]
    assert sorted(s["name"] for s in client.get("/api/sections").json()) == ["CSE-A", "CSE-B"]
    assert sorted(r["id"] for r in client.get("/api/rooms").json()) == ["R101", "R202"]


def test_rows_with_problems_are_reported_by_line_and_nothing_is_changed(
    client: TestClient,
) -> None:
    upload(client, VALID)
    bad = HEADER + (
        "Mon,10:00,10:50,R101,CSE-A,Chemistry,Dr New\n"  # line 2: fine
        "Funday,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"  # line 3: bad day
        "Tue,25:00,09:50,R101,CSE-A,Physics,Dr Iyer\n"  # line 4: bad start time
        "Wed,11:00,10:00,R101,CSE-A,Physics,Dr Iyer\n"  # line 5: ends before it starts
        "Thu,09:00,09:50,,CSE-A,Physics,Dr Iyer\n"  # line 6: no room
        "Mon,10:30,11:20,R101,CSE-B,Biology,Dr Rao\n"  # line 7: overlaps line 2 in R101
    )
    result = upload(client, bad)
    assert result["applied"] is False
    assert [e["row"] for e in result["errors"]] == [3, 4, 5, 6, 7]
    messages = {e["row"]: e["message"] for e in result["errors"]}
    assert "Funday" in messages[3]
    assert "start" in messages[4]
    assert "after start" in messages[5]
    assert "room" in messages[6]
    assert "overlaps row 2" in messages[7]

    # The earlier template is untouched and nothing from the bad file was created.
    assert len(client.get("/api/timetable").json()["slots"]) == 3
    assert "Dr New" not in [t["name"] for t in client.get("/api/teachers").json()]


def test_a_file_without_the_expected_columns_is_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/timetable", files={"file": ("t.csv", b"day,start\nMon,10:00\n", "text/csv")}
    )
    assert resp.status_code == 422
    assert "Missing column(s): end, room, section, subject, teacher" in resp.json()["detail"]


def test_a_new_upload_replaces_the_template(client: TestClient) -> None:
    upload(client, VALID)
    result = upload(
        client,
        HEADER
        + "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\nFri,14:00,14:50,R101,CSE-A,Biology,Dr Rao\n",
    )
    assert result["applied"] is True
    assert (
        result["slots_total"],
        result["slots_kept"],
        result["slots_added"],
        result["slots_removed"],
    ) == (2, 1, 1, 2)
    days = [s["day"] for s in client.get("/api/timetable").json()["slots"]]
    assert days == ["Mon", "Fri"]


def test_uploading_the_same_file_again_keeps_every_slot(client: TestClient) -> None:
    upload(client, VALID)
    before = [s["id"] for s in client.get("/api/timetable").json()["slots"]]
    result = upload(client, VALID)
    assert (result["slots_kept"], result["slots_added"], result["slots_removed"]) == (3, 0, 0)
    assert result["created"] == {"teachers": 0, "subjects": 0, "rooms": 0, "sections": 0}
    assert [s["id"] for s in client.get("/api/timetable").json()["slots"]] == before


def test_names_match_existing_entities_ignoring_case_and_extra_spaces(client: TestClient) -> None:
    upload(client, VALID)
    result = upload(client, HEADER + "mon,10:00,10:50,r101,cse-a,physics,  dr   iyer \n")
    assert result["created"] == {"teachers": 0, "subjects": 0, "rooms": 0, "sections": 0}
    assert len(client.get("/api/teachers").json()) == 2


def test_timetable_lists_the_time_zone_it_is_written_in(client: TestClient) -> None:
    assert client.get("/api/timetable").json()["timezone"] == "Asia/Kolkata"


def test_registering_a_camera_to_a_room_creates_the_room_and_can_move_the_camera(
    client: TestClient,
) -> None:
    resp = client.put("/api/cameras/CAM2", json={"room_id": "R202"})
    assert resp.status_code == 200
    assert resp.json() == {
        "id": "CAM2",
        "room_id": "R202",
        "camera_created": True,
        "room_created": True,
        "simulated": False,
    }

    moved = client.put("/api/cameras/CAM2", json={"room_id": "R101"}).json()
    assert moved["camera_created"] is False and moved["room_created"] is False
    rooms = {r["id"]: r["cameras"] for r in client.get("/api/rooms").json()}
    assert rooms == {"R101": ["CAM1", "CAM2"], "R202": []}
