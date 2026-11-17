"""The admin overview, through the backend HTTP API.

It uses the same hand-built cohort as test_teacher_feedback_api (Dr Iyer Physics at 10:00 and Dr Rao
Mathematics at 11:00 to CSE-A, five Mondays, S1 to S3) and checks the numbers by hand.

Every window has 30 seconds in view, a lecture is 60 windows. Per window "attention p" means p% of
the 30 seconds attentive and the rest distracted.

Dr Iyer's lectures (S1 at 80, S2 at 90, S3 at 90, S3 away on the last Monday):
  attentive  = 60 windows x (24 x 5 lectures + 27 x 5 + 27 x 4) = 21780 s
  distracted = 60 windows x (6 x 5 + 3 x 5 + 3 x 4)             =  3420 s
  class average = 21780 / 25200                                  = 0.864286
Dr Rao's lectures (60, 70, 70, S3 away on the same day):
  attentive 16740 s, distracted 8460 s, class average 16740 / 25200 = 0.664286

CSE-A has 3 students and there are 10 lectures. Each lecture counts the students in view for at
least half of its 50 minutes: 8 lectures with all 3 and 2 (the last Monday) with 2, so 28 of 30.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from simulator.client import Backend, seed
from simulator.config import load_simulator_config
from tests.conftest import FakeClock
from tests.test_student_profile_api import send
from tests.test_teacher_feedback_api import (  # noqa: F401  (cohort is a fixture)
    TIMETABLE,
    cohort,
    count_statements,
    seed_cohort,
)
from tests.test_timetable_api import upload


NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


def overview(client: TestClient) -> dict[str, Any]:
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def teachers(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["teacher"]: t for t in body["teachers"]}


# ---- the plain class average ----------------------------------------------------------------------------


def test_the_class_average_is_attention_from_the_summed_seconds_of_all_the_teachers_lectures(
    cohort: TestClient,
) -> None:
    rows = teachers(overview(cohort))
    assert rows["Dr Iyer"]["class_average"] == pytest.approx(21780 / 25200)
    assert rows["Dr Rao"]["class_average"] == pytest.approx(16740 / 25200)


def test_the_all_teachers_list_carries_the_class_average_too(cohort: TestClient) -> None:
    rows = {r["teacher"]: r for r in cohort.get("/api/teachers/feedback").json()}
    assert rows["Dr Iyer"]["class_average"] == pytest.approx(21780 / 25200)


def test_a_teacher_with_few_lectures_still_has_a_class_average_but_hidden_figures(
    client: TestClient, clock: FakeClock
) -> None:
    seed_cohort(client, clock, rao_lectures=3)
    rao = teachers(overview(client))["Dr Rao"]
    assert rao["hidden"] is True and rao["n_lectures"] == 3
    assert rao["self_comparison"]["value"] is None
    # three Mondays with all of S1 (60), S2 (70) and S3 (70): attentive 3 x 3600 s of 3 x 5400 s
    assert rao["class_average"] == pytest.approx(2 / 3)


def test_the_class_average_has_the_usual_presence_gate_and_ignores_unassigned_records(
    client: TestClient,
) -> None:
    upload(client, TIMETABLE)
    # One Monday 10:00 lecture in which the student is in view for a third of the time: gated out.
    send(client, "2026-03-02T04:30:00", 60, student="STU001", attentive=5, not_visible=20)
    assert teachers(overview(client))["Dr Iyer"]["class_average"] is None
    # Records with no lecture (a Tuesday) change nothing, even when they are full of attention.
    send(client, "2026-03-03T04:30:00", 60, student="STU001", attentive=30)
    assert teachers(overview(client))["Dr Iyer"]["class_average"] is None


def test_teachers_come_alphabetically_with_their_figures_unchanged(cohort: TestClient) -> None:
    body = overview(cohort)
    assert [t["teacher"] for t in body["teachers"]] == ["Dr Iyer", "Dr Rao"]
    assert body["min_lectures"] == 5 and body["range_level"] == 0.95
    assert teachers(body)["Dr Iyer"]["self_comparison"]["value"] == pytest.approx(10.0)
    assert teachers(body)["Dr Rao"]["self_comparison"]["value"] == pytest.approx(-10.0)


# ---- attendance by section and unassigned records ------------------------------------------------------------


def test_attendance_by_section_is_the_present_rate_across_its_lectures_and_its_size(
    cohort: TestClient,
) -> None:
    sections = overview(cohort)["sections"]
    assert [s["section"] for s in sections] == ["CSE-A"]
    cse = sections[0]
    assert cse["n_students"] == 3
    assert cse["n_lectures"] == 10
    assert (cse["present"], cse["expected"]) == (28, 30)
    assert cse["rate"] == pytest.approx(28 / 30)


def test_a_section_with_no_lectures_has_no_rate(client: TestClient) -> None:
    upload(client, TIMETABLE)
    client.post("/api/students", json={"register_no": "S1", "section": "CSE-A"})
    cse = overview(client)["sections"][0]
    assert cse["section"] == "CSE-A" and cse["n_students"] == 1
    assert cse["n_lectures"] == 0 and cse["rate"] is None


def test_unassigned_records_are_counted_in_windows(cohort: TestClient) -> None:
    assert overview(cohort)["unassigned_windows"] == 0
    send(cohort, "2026-03-03T04:30:00", 5, student="S1", distracted=30)  # a Tuesday: no slot, no lecture
    send(cohort, "2026-03-04T04:30:00", 2, student="S2", attentive=30)
    assert overview(cohort)["unassigned_windows"] == 7


def test_an_empty_database_gives_an_empty_overview(client: TestClient) -> None:
    body = overview(client)
    assert body["teachers"] == [] and body["sections"] == [] and body["unassigned_windows"] == 0


# ---- nothing about an individual student -------------------------------------------------------------------------


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


ALLOWED_KEYS = {
    "teachers", "sections", "unassigned_windows", "min_lectures", "range_level", "timezone", "now",
    "teacher_id", "teacher", "simulated", "n_lectures", "hidden", "reason", "class_average",
    "self_comparison", "slot_adjusted", "value", "low", "high", "n_students", "attendance",
    "present", "expected", "rate", "section_id", "section", "marks", "n_tests",
}  # fmt: skip


def test_the_admin_responses_hold_no_attention_figure_for_any_student(cohort: TestClient) -> None:
    send(cohort, "2026-03-03T04:30:00", 5, student="S1", distracted=30)
    for path in ("/api/admin/overview", "/api/teachers/feedback"):
        resp = cohort.get(path)
        assert resp.status_code == 200
        keys = keys_of(resp.json())
        assert keys <= ALLOWED_KEYS, keys - ALLOWED_KEYS
        assert not {k for k in keys if "student" in k and k != "n_students"}
        assert not {"register_no", "name", "lectures", "figure", "attentive", "distracted"} & keys
        for register_no in ("S1", "S2", "S3", "STU001"):
            assert f'"{register_no}"' not in resp.text


# ---- efficiency -----------------------------------------------------------------------------------------------


def test_the_overview_reads_the_records_in_one_grouped_pass(cohort: TestClient) -> None:
    with count_statements(cohort) as statements:
        assert cohort.get("/api/admin/overview").status_code == 200
    assert len(statements) <= 8
    grouped = [s for s in statements if "attention_records" in s and "GROUP BY" in s.upper()]
    assert len(grouped) == 1


# ---- the simulated cohort: does each figure find the built-in best teacher? ----------------------------------------

def test_on_the_small_simulated_cohort_the_slot_adjusted_figure_finds_the_built_in_best_teacher_and_the_class_average_does_not(
    client: TestClient, clock: FakeClock
) -> None:
    """One week, 6 students a section, the simulator's default effects and its default seed.

    Dr. Anita Menon is built +6 but teaches four of her five lectures at 14:00 (built -12), and Prof.
    Rahul Bhat teaches only the better section in the mornings. So the plain class average puts Bhat
    first; the slot-adjusted figure puts Menon first. The other teachers' true effect is 0.
    """
    clock.set(NOW)
    config = replace(load_simulator_config(small=True), records_per_request=1000)
    seed(Backend(client), config, now=NOW)
    rows = teachers(overview(client))
    best = max(
        (e for e in client.get("/api/simulation").json()["effects"] if e["kind"] == "teacher"),
        key=lambda e: e["value"],
    )["key"]
    assert best == "Dr. Anita Menon"
    assert all(not r["hidden"] for r in rows.values())  # every teacher has the five lectures

    plain = {name: r["class_average"] for name, r in rows.items()}
    own = {name: r["self_comparison"]["value"] for name, r in rows.items()}
    adjusted = {name: r["slot_adjusted"]["value"] for name, r in rows.items()}
    print("class average  ", {k: round(v, 4) for k, v in plain.items()})
    print("vs own average ", {k: round(v, 2) for k, v in own.items()})
    print("slot-adjusted  ", {k: round(v, 2) for k, v in adjusted.items()})

    assert max(plain, key=lambda name: plain[name]) != best
    assert max(plain, key=lambda name: plain[name]) == "Prof. Rahul Bhat"
    assert max(adjusted, key=lambda name: adjusted[name]) == best
    assert max(own, key=lambda name: own[name]) != best  # the self-comparison alone is not enough either
