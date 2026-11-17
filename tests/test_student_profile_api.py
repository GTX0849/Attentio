"""The student profile and current-lecture endpoints, through the backend HTTP API.

Records are seeded through POST /api/records on a Monday timetable in
Asia/Kolkata (10:00 local is 04:30 UTC), with the fake clock set explicitly.
Expected numbers are worked out by hand in the comments.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Student
from tests.conftest import FakeClock
from tests.test_timetable_api import HEADER, upload

# Mondays: 2026-02-23, 03-02, 03-09, 03-16, 03-23. Physics is 10:00-10:50 local (04:30-05:20 UTC,
# 3000 s, 100 windows), Mathematics 11:00-11:50 local (05:30-06:20 UTC).
TIMETABLE = HEADER + (
    "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
    "Mon,11:00,11:50,R101,CSE-A,Mathematics,Dr Rao\n"
)
PHYSICS = "04:30:00"
MATHS = "05:30:00"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def send(
    client: TestClient,
    start: str,
    count: int,
    *,
    student: str = "STU001",
    camera: str = "CAM1",
    **seconds: float,
) -> None:
    """`count` consecutive 30-second windows from `start` (UTC), each with the same seconds."""
    first = utc(start)
    records = [
        {
            "camera_id": camera,
            "student_id": student,
            "window_start": (first + timedelta(seconds=30 * i)).isoformat(),
            **seconds,
        }
        for i in range(count)
    ]
    resp = client.post("/api/records", json=records)
    assert resp.status_code == 200, resp.text


def profile(client: TestClient, student: str = "STU001") -> dict[str, Any]:
    resp = client.get(f"/api/students/{student}/profile")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def current(client: TestClient, student: str = "STU001") -> dict[str, Any]:
    resp = client.get(f"/api/students/{student}/current")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in rows}


@pytest.fixture
def semester(client: TestClient, clock: FakeClock) -> TestClient:
    """A semester of records for STU001, "now" being Wednesday 2026-03-25 09:30 local.

    Physics (Dr Iyer), 100 windows each unless noted:
      02-23  25 attentive, 5 distracted               -> 2500 att, 500 dist
      03-02  20 attentive, 5 distracted, 5 not visible -> 2000 att, 500 dist, 500 nv
      03-09  30 windows of 5 attentive, 25 not visible -> 150 att, 750 nv (barely in view)
      03-16  15 attentive, 15 distracted               -> 1500 att, 1500 dist
    Mathematics (Dr Rao), 03-02: 40 windows of 30 attentive -> 1200 att (20 of 50 minutes)
    Unassigned: Tuesday 03-03 04:30 UTC, one window of 30 distracted (no slot then)
    """
    upload(client, TIMETABLE)
    send(client, f"2026-02-23T{PHYSICS}", 100, attentive=25, distracted=5)
    send(client, f"2026-03-02T{PHYSICS}", 100, attentive=20, distracted=5, not_visible=5)
    send(client, f"2026-03-09T{PHYSICS}", 30, attentive=5, not_visible=25)
    send(client, f"2026-03-16T{PHYSICS}", 100, attentive=15, distracted=15)
    send(client, f"2026-03-02T{MATHS}", 40, attentive=30)
    send(client, "2026-03-03T04:30:00", 1, distracted=30)
    clock.set(utc("2026-03-25T04:00:00"))
    return client


# ---- breakdowns --------------------------------------------------------------------


def test_attention_by_subject_is_worked_from_summed_seconds(semester: TestClient) -> None:
    subjects = by_name(profile(semester)["by_subject"])
    assert set(subjects) == {"Physics", "Mathematics"}

    physics = subjects["Physics"]["figure"]
    # attentive 2500 + 2000 + 150 + 1500 = 6150; distracted 500 + 500 + 0 + 1500 = 2500
    assert physics["attentive"] == 6150
    assert physics["distracted"] == 2500
    assert physics["not_visible"] == 1250  # 500 + 750
    assert physics["value"] == pytest.approx(6150 / 8650)
    assert physics["presence"] == pytest.approx(8650 / 9900)
    assert physics["reason"] is None

    maths = subjects["Mathematics"]["figure"]
    assert maths["value"] == pytest.approx(1.0)  # 1200 attentive, 0 distracted


def test_the_unassigned_record_is_left_out_of_subject_and_teacher_figures(
    semester: TestClient,
) -> None:
    body = profile(semester)
    distracted = sum(row["figure"]["distracted"] for row in body["by_subject"])
    assert distracted == 2500  # the 30 s unassigned window is not in it
    assert sum(row["figure"]["distracted"] for row in body["by_teacher"]) == 2500
    assert sum(row["figure"]["distracted"] for row in body["attendance"]) == 2500


def test_attention_by_teacher_groups_the_lectures_of_each_teacher(semester: TestClient) -> None:
    teachers = by_name(profile(semester)["by_teacher"])
    assert set(teachers) == {"Dr Iyer", "Dr Rao"}
    assert teachers["Dr Iyer"]["figure"]["value"] == pytest.approx(6150 / 8650)
    assert teachers["Dr Rao"]["figure"]["value"] == pytest.approx(1.0)


def test_attention_by_time_of_day_uses_the_local_hour_and_includes_unassigned_time(
    semester: TestClient,
) -> None:
    hours = {row["hour"]: row for row in profile(semester)["by_time_of_day"]}
    assert sorted(hours) == [10, 11]  # 04:30 UTC is 10:00 local; 05:30 UTC is 11:00 local
    assert hours[10]["label"] == "10:00"
    # Physics 6150 attentive and 2500 distracted, plus the unassigned 30 distracted
    assert hours[10]["figure"]["value"] == pytest.approx(6150 / (6150 + 2530))
    assert hours[11]["figure"]["value"] == pytest.approx(1.0)


def test_a_subject_in_view_less_than_half_the_time_has_no_attention_value(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, TIMETABLE)
    # 10 attentive of 30 recorded: presence 33%, below the 50% gate
    send(client, f"2026-03-02T{PHYSICS}", 100, attentive=10, not_visible=20)
    clock.set(utc("2026-03-02T07:00:00"))
    physics = by_name(profile(client)["by_subject"])["Physics"]["figure"]
    assert physics["value"] is None
    assert physics["reason"] == "low_presence"
    assert physics["presence"] == pytest.approx(1 / 3)


# ---- weekly trend --------------------------------------------------------------------


def test_weekly_trend_leaves_weeks_that_fail_the_gate_or_have_no_records_null(
    semester: TestClient,
) -> None:
    weeks = profile(semester)["weekly"]
    assert [w["week_start"] for w in weeks] == [
        "2026-02-23",
        "2026-03-02",
        "2026-03-09",
        "2026-03-16",
        "2026-03-23",
    ]
    values = [w["figure"]["value"] for w in weeks]
    assert values[0] == pytest.approx(2500 / 3000)
    # 03-02 week: attentive 2000 + 1200, distracted 500 + 30 (unassigned counts here)
    assert values[1] == pytest.approx(3200 / 3730)
    assert values[2] is None  # 150 s in view of 900 recorded
    assert weeks[2]["figure"]["reason"] == "low_presence"
    assert values[3] == pytest.approx(0.5)
    assert values[4] is None  # the current week, nothing recorded yet
    assert weeks[4]["figure"]["reason"] == "no_records"


def test_the_overall_figure_sums_every_record_including_unassigned_ones(
    semester: TestClient,
) -> None:
    overall = profile(semester)["overall"]
    # attentive 6150 + 1200 = 7350; distracted 2500 + 30 = 2530; not visible 1250
    assert overall["attentive"] == 7350
    assert overall["distracted"] == 2530
    assert overall["value"] == pytest.approx(7350 / 9880)
    assert overall["presence"] == pytest.approx(9880 / 11130)


# ---- attendance ----------------------------------------------------------------------


def test_attendance_lists_each_lecture_with_presence_over_its_full_length(
    semester: TestClient,
) -> None:
    rows = profile(semester)["attendance"]  # newest first
    summary = [
        (r["lecture"]["start"][:10], r["lecture"]["subject"], round(r["presence"], 4), r["present"])
        for r in rows
    ]
    assert summary == [
        ("2026-03-16", "Physics", 1.0, True),  # 3000 s in view of 3000
        ("2026-03-09", "Physics", 0.05, False),  # 150 s of 3000
        ("2026-03-02", "Mathematics", 0.4, False),  # 1200 s of 3000: half the records are missing
        ("2026-03-02", "Physics", 0.8333, True),  # 2500 s of 3000
        ("2026-02-23", "Physics", 1.0, True),
    ]
    assert rows[0]["lecture"]["teacher"] == "Dr Iyer"
    assert all(r["in_progress"] is False for r in rows)


def test_a_lecture_with_half_its_records_missing_is_not_present(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, TIMETABLE)
    # 49 windows = 1470 s of a 3000 s lecture is 49%; 50 windows = 1500 s is exactly 50%
    send(client, f"2026-03-02T{PHYSICS}", 49, attentive=30)
    send(client, f"2026-03-09T{PHYSICS}", 50, attentive=30)
    clock.set(utc("2026-03-10T00:00:00"))
    rows = {r["lecture"]["start"][:10]: r for r in profile(client)["attendance"]}
    assert rows["2026-03-02"]["present"] is False
    assert rows["2026-03-09"]["present"] is True


def test_monthly_attendance_counts_present_lectures_of_the_month(semester: TestClient) -> None:
    month = profile(semester)["month"]
    # March has 4 lectures with records (03-02 x2, 03-09, 03-16); 03-02 Physics and 03-16 are present.
    # The February lecture is not counted.
    assert month["present"] == 2
    assert month["total"] == 4
    assert month["label"] == "March 2026"


def test_the_month_follows_the_injected_clock(semester: TestClient, clock: FakeClock) -> None:
    clock.set(utc("2026-02-25T04:00:00"))
    month = profile(semester)["month"]
    assert (month["present"], month["total"], month["label"]) == (1, 1, "February 2026")


def test_the_threshold_used_is_reported(semester: TestClient) -> None:
    assert profile(semester)["min_presence_ratio"] == 0.5
    assert profile(semester)["timezone"] == "Asia/Kolkata"


def test_a_student_with_no_records_gets_empty_but_valid_answers(client: TestClient, clock: FakeClock) -> None:
    clock.set(utc("2026-03-25T04:00:00"))
    body = profile(client)
    assert body["by_subject"] == [] and body["by_teacher"] == []
    assert body["by_time_of_day"] == [] and body["weekly"] == [] and body["attendance"] == []
    assert body["overall"]["value"] is None and body["overall"]["reason"] == "no_records"
    assert body["month"]["total"] == 0

    live = current(client)
    assert live["lecture"] is None
    assert live["attention_last_5_min"]["value"] is None
    assert live["attention_average"]["value"] is None
    assert live["presence_this_lecture"] is None
    assert live["distracted_this_lecture"] is None
    assert live["month"]["total"] == 0


def test_unknown_student_returns_404(client: TestClient) -> None:
    assert client.get("/api/students/NOPE/profile").status_code == 404
    assert client.get("/api/students/NOPE/current").status_code == 404


# ---- current lecture -----------------------------------------------------------------


@pytest.fixture
def mid_lecture(client: TestClient, clock: FakeClock) -> TestClient:
    """Monday 03-02, 34 minutes into Physics (now 05:04 UTC, 10:34 local).

    68 windows from 04:30 to 05:04:
      first 58 (04:30 to 04:59): 20 attentive, 5 distracted, 5 not visible
      last 10 (04:59 to 05:04, the last five minutes): 6 of 30 attentive, 4 of 30 distracted
    plus one unassigned window at 03:00 UTC (08:30 local): 30 distracted.
    """
    upload(client, TIMETABLE)
    send(client, f"2026-03-02T{PHYSICS}", 58, attentive=20, distracted=5, not_visible=5)
    send(client, "2026-03-02T04:59:00", 6, attentive=30)
    send(client, "2026-03-02T05:02:00", 4, distracted=30)
    send(client, "2026-03-02T03:00:00", 1, distracted=30)
    clock.set(utc("2026-03-02T05:04:00"))
    return client


def test_current_lecture_stats_for_the_live_header(mid_lecture: TestClient) -> None:
    live = current(mid_lecture)
    assert live["lecture"]["subject"] == "Physics"
    assert live["lecture"]["teacher"] == "Dr Iyer"
    assert live["now"].startswith("2026-03-02T05:04:00")

    # Last five minutes: 6 windows of 30 attentive, 4 of 30 distracted -> 180 / 300 = 60%
    five = live["attention_last_5_min"]
    assert five["attentive"] == 180 and five["distracted"] == 120
    assert five["value"] == pytest.approx(0.6)

    # Overall: attentive 58 x 20 + 180 = 1340; distracted 58 x 5 + 120 + 30 (unassigned) = 440
    assert live["attention_average"]["value"] == pytest.approx(1340 / 1780)

    # This lecture: 68 windows, 58 x 25 + 300 = 1750 s in view, 34 minutes = 2040 s elapsed
    presence = live["presence_this_lecture"]
    assert presence["visible_seconds"] == 1750
    assert presence["elapsed_seconds"] == 2040
    assert presence["ratio"] == pytest.approx(1750 / 2040)

    # Distracted in the lecture: 58 x 5 + 120 = 410 s (the unassigned 30 s is not in it)
    distracted = live["distracted_this_lecture"]
    assert distracted["seconds"] == 410
    assert distracted["share_of_visible"] == pytest.approx(410 / 1750)


def test_current_month_attendance_counts_the_running_lecture_against_its_full_length(
    mid_lecture: TestClient,
) -> None:
    month = current(mid_lecture)["month"]
    # 1750 s in view of the scheduled 3000 s is 58%: present so far
    assert (month["present"], month["total"]) == (1, 1)
    assert month["min_presence_ratio"] == 0.5


def test_the_last_five_minutes_gate_on_presence(client: TestClient, clock: FakeClock) -> None:
    upload(client, TIMETABLE)
    # Last five minutes: 10 windows of 5 attentive and 25 not visible, presence 17%
    send(client, "2026-03-02T04:59:00", 10, attentive=5, not_visible=25)
    clock.set(utc("2026-03-02T05:04:00"))
    five = current(client)["attention_last_5_min"]
    assert five["value"] is None
    assert five["reason"] == "low_presence"


def test_unassigned_records_count_toward_the_live_figures_without_a_current_lecture(
    client: TestClient, clock: FakeClock
) -> None:
    client.put("/api/cameras/CAM2", json={"room_id": "R202"})  # R202 has no timetable
    send(client, "2026-03-02T04:59:00", 10, camera="CAM2", attentive=20, distracted=10)
    clock.set(utc("2026-03-02T05:04:00"))
    live = current(client)
    assert live["lecture"] is None
    assert live["presence_this_lecture"] is None
    assert live["attention_last_5_min"]["value"] == pytest.approx(20 / 30)
    assert live["attention_average"]["value"] == pytest.approx(20 / 30)
    assert profile(client)["by_subject"] == []


def test_a_lecture_that_has_ended_is_not_the_current_one(
    mid_lecture: TestClient, clock: FakeClock
) -> None:
    clock.set(utc("2026-03-02T06:00:00"))  # 11:30 local, Physics ended at 10:50
    assert current(mid_lecture)["lecture"] is None


def test_an_open_manual_session_is_measured_up_to_now(client: TestClient, clock: FakeClock) -> None:
    upload(client, TIMETABLE)  # creates the teachers and subjects to pick from
    teacher = client.get("/api/teachers").json()[0]["id"]
    subject = client.get("/api/subjects").json()[0]["id"]
    clock.set(utc("2026-03-04T05:00:00"))
    started = client.post(
        "/api/sessions", json={"room_id": "R101", "teacher_id": teacher, "subject_id": subject}
    )
    assert started.status_code == 201
    clock.advance(minutes=20)
    # 20 minutes in view of the 20 elapsed: the open session is measured to now, not to a planned end
    send(client, "2026-03-04T05:00:00", 40, attentive=30)
    live = current(client)
    assert live["lecture"]["open"] is True
    assert live["presence_this_lecture"]["elapsed_seconds"] == 1200
    assert live["presence_this_lecture"]["ratio"] == pytest.approx(1.0)
    row = profile(client)["attendance"][0]
    assert row["present"] is True and row["in_progress"] is True and row["presence"] == 1.0


# ---- isolation -----------------------------------------------------------------------


def test_a_students_profile_contains_nothing_about_other_students(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, TIMETABLE)
    with Session(cast(FastAPI, client.app).state.engine) as db:
        db.add(Student(register_no="STU002", name="Other Student"))
        db.commit()

    # Both students attend the 03-02 Physics lecture; only STU002 attends 03-09 and Mathematics.
    send(client, f"2026-03-02T{PHYSICS}", 100, attentive=30)
    send(client, f"2026-03-02T{PHYSICS}", 100, student="STU002", distracted=30)
    send(client, f"2026-03-09T{PHYSICS}", 100, student="STU002", distracted=30)
    send(client, f"2026-03-02T{MATHS}", 40, student="STU002", distracted=30)
    clock.set(utc("2026-03-10T00:00:00"))

    mine = profile(client, "STU001")
    assert mine["register_no"] == "STU001"
    assert "STU002" not in str(mine)
    assert [r["lecture"]["subject"] for r in mine["attendance"]] == ["Physics"]  # one lecture only
    assert mine["overall"]["attentive"] == 3000
    assert mine["overall"]["distracted"] == 0
    assert mine["overall"]["value"] == pytest.approx(1.0)
    assert by_name(mine["by_subject"])["Physics"]["figure"]["distracted"] == 0
    assert mine["month"]["total"] == 1
    # weeks of 03-02 and 03-09 (the current one, empty): none of STU002's distracted time in either
    assert [w["figure"]["distracted"] for w in mine["weekly"]] == [0, 0]

    theirs = profile(client, "STU002")
    assert theirs["overall"]["attentive"] == 0
    assert theirs["overall"]["distracted"] == 3000 + 3000 + 1200
    assert theirs["month"]["total"] == 3


def test_the_current_endpoint_ignores_other_students_too(client: TestClient, clock: FakeClock) -> None:
    upload(client, TIMETABLE)
    with Session(cast(FastAPI, client.app).state.engine) as db:
        db.add(Student(register_no="STU002"))
        db.commit()
    send(client, "2026-03-02T04:59:00", 10, student="STU002", distracted=30)
    clock.set(utc("2026-03-02T05:04:00"))
    live = current(client, "STU001")  # STU001 sent nothing
    assert live["lecture"] is None
    assert live["attention_last_5_min"]["reason"] == "no_records"
    assert live["month"]["total"] == 0
    assert current(client, "STU002")["distracted_this_lecture"]["seconds"] == 300
