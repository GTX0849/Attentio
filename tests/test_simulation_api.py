"""Simulated data through the backend HTTP API: the flag, scoped timetable replace, section
attendance, stored true effects and the wipe that leaves live data alone.

Mondays used: 2026-03-02 and 2026-03-09. 10:00 local (Asia/Kolkata) is 04:30 UTC, so a
50-minute slot is 100 windows of 30 seconds.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import FakeClock
from tests.test_student_profile_api import profile, send, utc
from tests.test_timetable_api import HEADER, upload

REAL_TIMETABLE = HEADER + "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
SIM_TIMETABLE = HEADER + (
    "Mon,10:00,10:50,SIM-R201,SIM-A,Algorithms,Dr Sim One\n"
    "Tue,10:00,10:50,SIM-R201,SIM-A,Algorithms,Dr Sim One\n"
)
PHYSICS = "04:30:00"


def upload_simulated(client: TestClient, csv_text: str) -> dict[str, Any]:
    resp = client.post(
        "/api/timetable",
        files={"file": ("sim.csv", csv_text.encode(), "text/csv")},
        data={"simulated": "true"},
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def add_student(client: TestClient, register_no: str, **fields: Any) -> dict[str, Any]:
    resp = client.post("/api/students", json={"register_no": register_no, **fields})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def sim_setup(client: TestClient) -> None:
    """A simulated section SIM-A in SIM-R201 with camera SIM-CAM-201 and students SIM-A01, SIM-A02."""
    upload_simulated(client, SIM_TIMETABLE)
    resp = client.put("/api/cameras/SIM-CAM-201", json={"room_id": "SIM-R201", "simulated": True})
    assert resp.status_code == 200, resp.text
    for number in ("SIM-A01", "SIM-A02"):
        add_student(client, number, name=f"Sim {number}", section="SIM-A", simulated=True)


def by_id(rows: list[dict[str, Any]], key: str = "id") -> dict[str, dict[str, Any]]:
    return {r[key]: r for r in rows}


# ---- the simulated flag ----------------------------------------------------------------


def test_a_student_can_be_registered_with_a_section_and_the_simulated_flag(
    client: TestClient,
) -> None:
    created = add_student(
        client,
        "SIM-A01",
        name="Asha Sim",
        section="SIM-A",
        simulated=True,
        archetype="engaged",
    )
    assert created == {
        "register_no": "SIM-A01",
        "name": "Asha Sim",
        "section": "SIM-A",
        "simulated": True,
        "archetype": "engaged",
    }
    students = by_id(client.get("/api/students").json(), "register_no")
    assert students["SIM-A01"]["archetype"] == "engaged"
    # The seeded live student is not simulated and has no section.
    assert students["STU001"]["simulated"] is False
    assert students["STU001"]["section"] is None
    # The section the student named was created, flagged as simulated.
    assert client.post(
        "/api/students", json={"register_no": "SIM-A01", "simulated": True}
    ).status_code == 409


def test_students_default_to_live_and_the_flag_shows_in_the_profile_and_current_answers(
    client: TestClient,
) -> None:
    add_student(client, "SIM-A01", simulated=True)
    add_student(client, "REAL2", name="Second Live Student")
    for number, expected in (("SIM-A01", True), ("REAL2", False), ("STU001", False)):
        assert client.get(f"/api/students/{number}/profile").json()["simulated"] is expected
        assert client.get(f"/api/students/{number}/current").json()["simulated"] is expected
        assert client.get(f"/api/students/{number}/live").json()["simulated"] is expected


def test_a_simulated_upload_flags_what_it_creates_and_the_flag_reaches_lectures_and_teachers(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, REAL_TIMETABLE)
    result = upload_simulated(client, SIM_TIMETABLE)
    assert result["created"] == {"teachers": 1, "subjects": 1, "rooms": 1, "sections": 1}

    teachers = {t["name"]: t["simulated"] for t in client.get("/api/teachers").json()}
    assert teachers == {"Dr Iyer": False, "Dr Sim One": True}
    rooms = {r["id"]: r["simulated"] for r in client.get("/api/rooms").json()}
    assert rooms == {"R101": False, "SIM-R201": True}
    slots = client.get("/api/timetable").json()["slots"]
    assert {s["room"]: s["simulated"] for s in slots} == {"R101": False, "SIM-R201": True}

    # A record in the simulated room makes a simulated lecture; one in R101 a live lecture.
    client.put("/api/cameras/SIM-CAM-201", json={"room_id": "SIM-R201", "simulated": True})
    add_student(client, "SIM-A01", section="SIM-A", simulated=True)
    send(client, f"2026-03-02T{PHYSICS}", 2, student="SIM-A01", camera="SIM-CAM-201", attentive=30)
    send(client, f"2026-03-02T{PHYSICS}", 2, attentive=30)
    clock.set(utc("2026-03-02T09:00:00"))
    lectures = client.get("/api/lectures").json()
    assert {x["room_id"]: x["simulated"] for x in lectures} == {"R101": False, "SIM-R201": True}


# ---- scoped timetable replace -------------------------------------------------------------


def test_a_simulated_upload_leaves_the_live_timetable_alone_and_a_live_one_leaves_the_simulated(
    client: TestClient,
) -> None:
    upload(client, REAL_TIMETABLE)
    sim = upload_simulated(client, SIM_TIMETABLE)
    assert (sim["slots_total"], sim["slots_added"], sim["slots_removed"]) == (2, 2, 0)
    assert len(client.get("/api/timetable").json()["slots"]) == 3

    # Replacing the simulated template removes only simulated slots.
    sim2 = upload_simulated(client, HEADER + "Wed,09:00,09:50,SIM-R201,SIM-A,Algorithms,Dr Sim One\n")
    assert (sim2["slots_total"], sim2["slots_added"], sim2["slots_removed"]) == (1, 1, 2)
    days = sorted((s["day"], s["simulated"]) for s in client.get("/api/timetable").json()["slots"])
    assert days == [("Mon", False), ("Wed", True)]

    # Replacing the live template removes only live slots.
    live = upload(client, HEADER + "Fri,09:00,09:50,R101,CSE-A,Physics,Dr Iyer\n")
    assert (live["slots_total"], live["slots_removed"]) == (1, 1)
    days = sorted((s["day"], s["simulated"]) for s in client.get("/api/timetable").json()["slots"])
    assert days == [("Fri", False), ("Wed", True)]


def test_uploading_the_same_simulated_file_again_keeps_its_slots(client: TestClient) -> None:
    upload_simulated(client, SIM_TIMETABLE)
    again = upload_simulated(client, SIM_TIMETABLE)
    assert (again["slots_kept"], again["slots_added"], again["slots_removed"]) == (2, 0, 0)


# ---- attendance by section -------------------------------------------------------------------


def test_a_student_with_a_section_is_counted_absent_for_the_section_lectures_without_records(
    client: TestClient, clock: FakeClock
) -> None:
    sim_setup(client)
    # SIM-A02 attends both Mondays; SIM-A01 only the first. Both lectures exist because of SIM-A02.
    for monday in ("2026-03-02", "2026-03-09"):
        send(client, f"{monday}T{PHYSICS}", 100, student="SIM-A02", camera="SIM-CAM-201", attentive=30)
    send(client, f"2026-03-02T{PHYSICS}", 100, student="SIM-A01", camera="SIM-CAM-201", attentive=30)
    clock.set(utc("2026-03-10T00:00:00"))

    rows = {r["lecture"]["start"][:10]: r for r in profile(client, "SIM-A01")["attendance"]}
    assert set(rows) == {"2026-03-02", "2026-03-09"}
    assert (rows["2026-03-02"]["present"], rows["2026-03-02"]["presence"]) == (True, 1.0)
    assert (rows["2026-03-09"]["present"], rows["2026-03-09"]["presence"]) == (False, 0.0)
    assert rows["2026-03-09"]["figure"]["reason"] == "no_records"
    month = profile(client, "SIM-A01")["month"]
    assert (month["present"], month["total"]) == (1, 2)
    # The profile still has attention figures only from the lecture the student was in.
    assert [r["name"] for r in profile(client, "SIM-A01")["by_teacher"]] == ["Dr Sim One"]
    # The student who never missed one is present at both.
    assert profile(client, "SIM-A02")["month"]["present"] == 2
    # And the same through the current endpoint's month count.
    assert client.get("/api/students/SIM-A01/current").json()["month"]["total"] == 2


def test_lectures_that_have_not_started_and_other_sections_do_not_count_for_a_section_student(
    client: TestClient, clock: FakeClock
) -> None:
    sim_setup(client)
    upload_simulated(
        client,
        SIM_TIMETABLE + "Mon,11:00,11:50,SIM-R202,SIM-B,Databases,Dr Sim Two\n",
    )
    client.put("/api/cameras/SIM-CAM-202", json={"room_id": "SIM-R202", "simulated": True})
    add_student(client, "SIM-B01", section="SIM-B", simulated=True)
    send(client, f"2026-03-02T{PHYSICS}", 100, student="SIM-A02", camera="SIM-CAM-201", attentive=30)
    send(client, "2026-03-02T05:30:00", 100, student="SIM-B01", camera="SIM-CAM-202", attentive=30)
    send(client, "2026-03-09T04:30:00", 100, student="SIM-A02", camera="SIM-CAM-201", attentive=30)
    clock.set(utc("2026-03-09T03:00:00"))  # the second Monday lecture has not started
    rows = profile(client, "SIM-A01")["attendance"]
    assert [r["lecture"]["start"][:10] for r in rows] == ["2026-03-02"]
    assert rows[0]["present"] is False


def test_a_student_without_a_section_keeps_counting_only_lectures_with_records(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, REAL_TIMETABLE)
    sim_setup(client)  # lectures exist for another section; STU001 has no section
    send(client, f"2026-03-02T{PHYSICS}", 100, student="SIM-A02", camera="SIM-CAM-201", attentive=30)
    send(client, f"2026-03-02T{PHYSICS}", 100, attentive=30)
    clock.set(utc("2026-03-10T00:00:00"))
    rows = profile(client, "STU001")["attendance"]
    assert [r["lecture"]["room_id"] for r in rows] == ["R101"]


# ---- the true effects ----------------------------------------------------------------------


def test_no_simulation_reads_back_as_null_and_effects_round_trip(client: TestClient) -> None:
    assert client.get("/api/simulation").json() is None

    effects = [
        {"kind": "teacher", "key": "Dr Sim One", "value": 6.0},
        {"kind": "teacher", "key": "Dr Sim Two", "value": 0.0},
        {"kind": "slot", "key": "14:00", "value": -12.0},
        {"kind": "section", "key": "SIM-A", "value": 5.0},
    ]
    resp = client.put("/api/simulation", json={"effects": effects, "weeks": 4, "seed": 7})
    assert resp.status_code == 200, resp.text

    body = client.get("/api/simulation").json()
    assert body["effects"] == effects
    assert body["weeks"] == 4
    assert body["seed"] == 7
    assert body["students"] == 0

    # A second PUT replaces the first.
    client.put("/api/simulation", json={"effects": effects[:1], "weeks": 2})
    again = client.get("/api/simulation").json()
    assert again["effects"] == effects[:1] and again["weeks"] == 2 and again["seed"] is None


def test_an_unknown_effect_kind_is_rejected(client: TestClient) -> None:
    resp = client.put(
        "/api/simulation", json={"effects": [{"kind": "weather", "key": "x", "value": 1}]}
    )
    assert resp.status_code == 422


def test_the_simulation_summary_counts_what_is_loaded(client: TestClient, clock: FakeClock) -> None:
    sim_setup(client)
    add_student(client, "SIM-A03", section="SIM-A", simulated=True, archetype="engaged")
    client.put("/api/simulation", json={"effects": [], "weeks": 4})
    send(client, f"2026-03-02T{PHYSICS}", 3, student="SIM-A01", camera="SIM-CAM-201", attentive=30)
    summary = client.get("/api/simulation").json()
    assert summary["students"] == 3
    assert summary["teachers"] == 1
    assert summary["sections"] == ["SIM-A"]
    assert summary["lectures"] == 1
    assert summary["records"] == 3
    assert summary["archetypes"] == {"engaged": 1}


# ---- wipe -------------------------------------------------------------------------------------


def test_wiping_the_simulation_removes_simulated_data_and_leaves_live_data_intact(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, REAL_TIMETABLE)
    sim_setup(client)
    client.put(
        "/api/simulation", json={"effects": [{"kind": "slot", "key": "10:00", "value": -1}]}
    )
    send(client, f"2026-03-02T{PHYSICS}", 100, student="SIM-A01", camera="SIM-CAM-201", attentive=30)
    send(client, f"2026-03-02T{PHYSICS}", 100, attentive=25, distracted=5)
    clock.set(utc("2026-03-10T00:00:00"))
    live_before = profile(client, "STU001")

    resp = client.delete("/api/simulation")
    assert resp.status_code == 200, resp.text
    removed = resp.json()
    assert removed["students"] == 2 and removed["records"] == 100

    assert client.get("/api/simulation").json() is None
    assert client.get("/api/students/SIM-A01/profile").status_code == 404
    assert [s["register_no"] for s in client.get("/api/students").json()] == ["STU001"]
    assert [r["id"] for r in client.get("/api/rooms").json()] == ["R101"]
    assert [t["name"] for t in client.get("/api/teachers").json()] == ["Dr Iyer"]
    assert [s["name"] for s in client.get("/api/sections").json()] == ["CSE-A"]
    assert [s["name"] for s in client.get("/api/subjects").json()] == ["Physics"]
    assert [s["room"] for s in client.get("/api/timetable").json()["slots"]] == ["R101"]
    assert [x["room_id"] for x in client.get("/api/lectures").json()] == ["R101"]

    # STU001, R101, CAM1 and the live records are untouched.
    assert profile(client, "STU001") == live_before
    assert client.put("/api/cameras/CAM1", json={"room_id": "R101"}).json()["camera_created"] is False
    assert client.put(
        "/api/cameras/SIM-CAM-201", json={"room_id": "SIM-R201", "simulated": True}
    ).json()["camera_created"] is True  # it was removed


def test_wiping_when_nothing_is_simulated_changes_nothing(client: TestClient) -> None:
    upload(client, REAL_TIMETABLE)
    resp = client.delete("/api/simulation")
    assert resp.status_code == 200
    assert resp.json()["students"] == 0
    assert len(client.get("/api/timetable").json()["slots"]) == 1
