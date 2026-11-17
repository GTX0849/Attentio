"""Teacher feedback endpoints, through the backend HTTP API.

The hand-built cohort uses the Monday timetable of Asia/Kolkata, as test_student_profile_api does
(10:00 local is 04:30 UTC, 11:00 local is 05:30 UTC). Dr Iyer teaches Physics at 10:00 and Dr Rao
Mathematics at 11:00, both to CSE-A, five Mondays: 02-23, 03-02, 03-09, 03-16, 03-23.

Every window has 30 seconds in view, so a lecture of 60 windows is 1800 of 3000 seconds, which is
present. "attention p" below means p% attentive and (100 - p)% distracted in every window.

  student   Iyer (10:00)   Rao (11:00)   own average   attended
  S1            80             60            70        every lecture
  S2            90             70            80        every lecture
  S3            90             70            80        every lecture except 03-23 (both teachers)

So against their own average each student is +10 in Iyer's lectures and -10 in Rao's. The 10:00 slot
averages +10 and the 11:00 slot -10: each slot is taught by one teacher only, so the slot-adjusted
figure is 0 for both (the limit the docs call out).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from simulator.client import Backend, seed
from simulator.config import load_simulator_config
from tests.conftest import FakeClock
from tests.test_student_profile_api import MATHS, PHYSICS, send
from tests.test_timetable_api import HEADER, upload

TIMETABLE = HEADER + (
    "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
    "Mon,11:00,11:50,R101,CSE-A,Mathematics,Dr Rao\n"
)
MONDAYS = ["2026-02-23", "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
ABSENT_DAY = "2026-03-23"
LEVELS = {"S1": (80, 60), "S2": (90, 70), "S3": (90, 70)}  # attention in Iyer's and Rao's lectures


def send_lecture(client: TestClient, start: str, student: str, percent: float) -> None:
    """A 60-window lecture for one student: `percent` attentive, the rest distracted, in every window."""
    send(client, start, 60, student=student, attentive=0.3 * percent, distracted=0.3 * (100 - percent))


def seed_cohort(client: TestClient, clock: FakeClock, rao_lectures: int = 5) -> None:
    upload(client, TIMETABLE)
    for register_no in LEVELS:
        resp = client.post("/api/students", json={"register_no": register_no, "section": "CSE-A"})
        assert resp.status_code == 201, resp.text
    for day in MONDAYS:
        for student, (iyer, rao) in LEVELS.items():
            if student == "S3" and day == ABSENT_DAY:
                continue
            send_lecture(client, f"{day}T{PHYSICS}", student, iyer)
            if day in MONDAYS[:rao_lectures]:
                send_lecture(client, f"{day}T{MATHS}", student, rao)
    clock.set(datetime(2026, 3, 25, 4, 0, tzinfo=UTC))


def teacher_id(client: TestClient, name: str) -> int:
    return int(next(t["id"] for t in client.get("/api/teachers").json() if t["name"] == name))


def feedback(client: TestClient, name: str) -> dict[str, Any]:
    resp = client.get(f"/api/teachers/{teacher_id(client, name)}/feedback")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


@pytest.fixture
def cohort(client: TestClient, clock: FakeClock) -> TestClient:
    seed_cohort(client, clock)
    return client


# ---- one teacher ------------------------------------------------------------------------------------


def test_a_teacher_gets_their_two_figures_with_ranges_and_student_counts(cohort: TestClient) -> None:
    iyer = feedback(cohort, "Dr Iyer")
    assert iyer["teacher"] == "Dr Iyer" and iyer["simulated"] is False
    assert iyer["n_lectures"] == 5
    assert iyer["hidden"] is False and iyer["reason"] is None
    assert iyer["min_lectures"] == 5 and iyer["range_level"] == 0.95
    own = iyer["self_comparison"]
    assert own["value"] == pytest.approx(10.0)
    assert own["n_students"] == 3
    assert own["low"] == pytest.approx(10.0) and own["high"] == pytest.approx(10.0)  # no spread
    slot = iyer["slot_adjusted"]
    assert slot["value"] == pytest.approx(0.0, abs=1e-9)  # the 10:00 slot is only ever Iyer's
    assert slot["n_students"] == 3

    assert feedback(cohort, "Dr Rao")["self_comparison"]["value"] == pytest.approx(-10.0)


def test_a_teachers_lectures_are_newest_first_with_attendance_and_attention(cohort: TestClient) -> None:
    iyer = feedback(cohort, "Dr Iyer")
    rows = iyer["lectures"]
    assert [r["lecture"]["start"][:10] for r in rows] == sorted(MONDAYS, reverse=True)
    newest = rows[0]
    assert newest["lecture"]["teacher"] == "Dr Iyer" and newest["lecture"]["section"] == "CSE-A"
    assert newest["lecture"]["subject"] == "Physics"
    assert (newest["present"], newest["expected"]) == (2, 3)  # S3 was away on 03-23
    # S1 80 and S2 90 in 60 windows each: attentive 24 + 27, distracted 6 + 3 per window pair
    assert newest["attention"]["value"] == pytest.approx(51 / 60)
    older = rows[1]
    assert (older["present"], older["expected"]) == (3, 3)
    assert older["attention"]["value"] == pytest.approx(78 / 90)  # (24 + 27 + 27) / (30 x 3)

    attendance = iyer["attendance"]
    assert (attendance["present"], attendance["expected"]) == (14, 15)
    assert attendance["rate"] == pytest.approx(14 / 15)


def test_a_teacher_with_fewer_than_five_lectures_has_hidden_figures(
    client: TestClient, clock: FakeClock
) -> None:
    seed_cohort(client, clock, rao_lectures=3)
    rao = feedback(client, "Dr Rao")
    assert rao["n_lectures"] == 3
    assert rao["hidden"] is True and rao["reason"] == "fewer than 5 lectures"
    for key in ("self_comparison", "slot_adjusted"):
        assert rao[key]["value"] is None and rao[key]["low"] is None and rao[key]["high"] is None
    assert len(rao["lectures"]) == 3  # the lectures themselves are still listed
    assert feedback(client, "Dr Iyer")["hidden"] is False


def test_a_teacher_with_no_lectures_gets_empty_hidden_figures(client: TestClient) -> None:
    upload(client, TIMETABLE)
    body = feedback(client, "Dr Iyer")
    assert body["n_lectures"] == 0 and body["hidden"] is True
    assert body["lectures"] == []
    assert body["attendance"] == {"present": 0, "expected": 0, "rate": None}


def test_an_unknown_teacher_is_a_404(client: TestClient) -> None:
    assert client.get("/api/teachers/999/feedback").status_code == 404


def test_the_answer_for_one_teacher_says_nothing_about_another(cohort: TestClient) -> None:
    iyer_id, rao_id = teacher_id(cohort, "Dr Iyer"), teacher_id(cohort, "Dr Rao")
    resp = cohort.get(f"/api/teachers/{iyer_id}/feedback")
    text = resp.text
    assert "Dr Rao" not in text and "Mathematics" not in text
    assert {r["lecture"]["teacher_id"] for r in resp.json()["lectures"]} == {iyer_id}
    assert resp.json()["teacher_id"] == iyer_id != rao_id
    rao_lecture_ids = {r["lecture"]["id"] for r in feedback(cohort, "Dr Rao")["lectures"]}
    assert not rao_lecture_ids & {r["lecture"]["id"] for r in resp.json()["lectures"]}


def test_records_with_no_lecture_do_not_change_the_figures(cohort: TestClient) -> None:
    before = feedback(cohort, "Dr Iyer")["self_comparison"]
    send(cohort, "2026-03-03T04:30:00", 5, student="S1", distracted=30)  # a Tuesday: no slot, no lecture
    after = feedback(cohort, "Dr Iyer")
    assert after["self_comparison"] == before
    assert after["n_lectures"] == 5


# ---- all teachers ---------------------------------------------------------------------------------------


def test_all_teachers_come_in_one_list_with_the_same_figures(cohort: TestClient) -> None:
    resp = cohort.get("/api/teachers/feedback")
    assert resp.status_code == 200
    rows = {r["teacher"]: r for r in resp.json()}
    assert set(rows) == {"Dr Iyer", "Dr Rao"}
    assert rows["Dr Iyer"]["self_comparison"]["value"] == pytest.approx(10.0)
    assert rows["Dr Rao"]["self_comparison"]["value"] == pytest.approx(-10.0)
    single = feedback(cohort, "Dr Iyer")
    for key in ("self_comparison", "slot_adjusted", "n_lectures", "hidden", "reason", "attendance"):
        assert rows["Dr Iyer"][key] == single[key]


@contextmanager
def count_statements(client: TestClient) -> Iterator[list[str]]:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_all_teachers_are_worked_in_a_few_queries_not_one_per_teacher(cohort: TestClient) -> None:
    with count_statements(cohort) as statements:
        assert cohort.get("/api/teachers/feedback").status_code == 200
    assert len(statements) <= 6
    # one pass over the records, grouped by student and lecture
    record_queries = [s for s in statements if "attention_records" in s]
    assert len(record_queries) == 1 and "GROUP BY" in record_queries[0].upper()


# ---- the in-lecture timeline ------------------------------------------------------------------------------


def test_a_lecture_timeline_is_class_attention_per_30_second_window(
    client: TestClient, clock: FakeClock
) -> None:
    upload(client, TIMETABLE)
    for register_no in ("S1", "S2"):
        client.post("/api/students", json={"register_no": register_no, "section": "CSE-A"})
    clock.set(datetime(2026, 3, 25, 4, 0, tzinfo=UTC))
    day = f"2026-03-02T{PHYSICS[:-2]}"  # 04:30
    # window 0: S1 30 attentive; S2 10 attentive + 20 distracted        -> (30 + 10) / (30 + 10 + 20) = 2/3
    send(client, f"{day}00", 1, student="S1", attentive=30)
    send(client, f"{day}00", 1, student="S2", attentive=10, distracted=20)
    # window 1: S1 only, 15 attentive + 15 distracted                   -> 1/2
    send(client, "2026-03-02T04:30:30", 1, student="S1", attentive=15, distracted=15)
    # window 3 (nothing in window 2): S2 only neutral, so no attention figure
    send(client, "2026-03-02T04:31:30", 1, student="S2", neutral=30)

    lecture_id = client.get("/api/lectures").json()[0]["id"]
    resp = client.get(f"/api/lectures/{lecture_id}/timeline")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lecture"]["id"] == lecture_id and body["window_seconds"] == 30
    points = body["points"]
    assert [p["window"] for p in points] == [0, 1, 3]
    assert [p["offset_seconds"] for p in points] == [0, 30, 90]
    assert [p["students"] for p in points] == [2, 1, 1]
    assert points[0]["attention"] == pytest.approx(2 / 3)
    assert points[1]["attention"] == pytest.approx(0.5)
    assert points[2]["attention"] is None  # neutral only


def test_a_timeline_for_an_unknown_lecture_is_a_404(client: TestClient) -> None:
    assert client.get("/api/lectures/999/timeline").status_code == 404


# ---- the simulated cohort ---------------------------------------------------------------------------------------

NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


def test_the_slot_adjusted_figure_puts_the_better_teacher_first_in_a_small_simulated_cohort(
    client: TestClient, clock: FakeClock
) -> None:
    """One week, 6 students a section, Dr. Anita Menon built +6 and teaching four of five at 14:00 (-12)."""
    clock.set(NOW)
    config = replace(load_simulator_config(small=True), records_per_request=1000)
    seed(Backend(client), config, now=NOW)

    rows = {r["teacher"]: r for r in client.get("/api/teachers/feedback").json()}
    menon, bhat = rows["Dr. Anita Menon"], rows["Prof. Rahul Bhat"]
    assert menon["simulated"] is True
    assert not menon["hidden"] and menon["n_lectures"] == 5

    plain = {name: r["self_comparison"]["value"] for name, r in rows.items()}
    adjusted = {name: r["slot_adjusted"]["value"] for name, r in rows.items()}
    print("self-comparison", {k: round(v, 2) for k, v in plain.items()})
    print("slot-adjusted  ", {k: round(v, 2) for k, v in adjusted.items()})
    assert max(adjusted, key=lambda name: adjusted[name]) == "Dr. Anita Menon"
    assert 1 < adjusted["Dr. Anita Menon"] < 9  # built in at +6; a small cohort measures about +4
    assert all(abs(v) < 5 for name, v in adjusted.items() if name != "Dr. Anita Menon")
    assert plain["Dr. Anita Menon"] < 0  # the afternoon slot drags the plain self-comparison down
    # Most of the 14:00 penalty is taken back. Her slot-adjusted figure beats her plain one by a lot.
    assert adjusted["Dr. Anita Menon"] - plain["Dr. Anita Menon"] > 6
    assert plain["Prof. Rahul Bhat"] > plain["Dr. Anita Menon"]  # the plain figure would rank Bhat first
    assert bhat["simulated"] is True
