"""The simulator's marks: the pure generation (no server), then a small cohort seeded through the API.

`NOW` is a Wednesday afternoon (UTC), so "four weeks ending today" is the same days on every run:
Thu 19 Feb to Wed 18 Mar, twenty weekdays. Split in two blocks of ten days, the tests are dated
Wed 4 March (the tenth lecture day) and Wed 18 March (the twentieth).
"""

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend import marks
from simulator.client import Backend, seed
from simulator.config import SimulatorConfig, load_simulator_config
from simulator.generate import (
    SimStudent,
    SimTest,
    build_students,
    lecture_schedule,
    marks_csv,
    marks_for,
    marks_schedule,
)
from tests.conftest import FakeClock

KOLKATA = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)  # Wednesday 17:30 local, after every lecture of the day


@pytest.fixture(scope="module")
def config() -> SimulatorConfig:
    return load_simulator_config()


@pytest.fixture(scope="module")
def students(config: SimulatorConfig) -> list[SimStudent]:
    return build_students(config)


@pytest.fixture(scope="module")
def tests(config: SimulatorConfig) -> list[SimTest]:
    return marks_schedule(config, KOLKATA, NOW)


# ---- the schedule of tests ---------------------------------------------------------------------------


def test_every_teacher_and_section_pairing_has_two_tests_dated_at_the_end_of_each_half(tests: list[SimTest]) -> None:
    # Section SIM-A is taught by all five teachers, SIM-B by four (not Prof. Rahul Bhat): 9 pairings.
    pairings = Counter((t.teacher, t.section) for t in tests)
    assert len(pairings) == 9 and set(pairings.values()) == {2}
    assert len(tests) == 18
    assert {t.date for t in tests} == {date(2026, 3, 4), date(2026, 3, 18)}
    assert {(t.number, t.max_marks) for t in tests} == {(1, 50.0), (2, 100.0)}
    assert "Mid-term 1 (SIM-A)" in {t.name for t in tests}
    # A test is one teacher, one subject, one date and one name: no two the same.
    assert len({(t.teacher, t.subject, t.date, t.name) for t in tests}) == len(tests)


def test_the_two_tests_of_a_pairing_split_its_lectures_so_each_lecture_is_covered_once_and_before_its_test_date(
    config: SimulatorConfig, tests: list[SimTest]
) -> None:
    lectures = lecture_schedule(config, KOLKATA, NOW)
    by_pairing: dict[tuple[str, str], list[SimTest]] = defaultdict(list)
    for t in tests:
        by_pairing[(t.teacher, t.section)].append(t)
    for (teacher, section), pair in by_pairing.items():
        own = [x for x in lectures if x.slot.teacher == teacher and x.slot.section == section]
        first, second = sorted(pair, key=lambda t: t.date)
        # Ids are the lecture's position; the default coverage rule decides which test takes which.
        refs = [marks.LectureRef(i, 1, 1, 1, x.date) for i, x in enumerate(own)]
        covered_first = marks.covered_lectures(refs, teacher=1, subject=1, test_date=first.date, previous={1: None})
        covered_second = marks.covered_lectures(refs, teacher=1, subject=1, test_date=second.date, previous={1: first.date})
        assert sorted(covered_first + covered_second) == list(range(len(own)))
        assert covered_first and covered_second
        assert all(own[i].date <= first.date for i in covered_first)
        assert all(first.date < own[i].date <= second.date for i in covered_second)


def test_a_one_week_run_still_gets_two_tests_per_pairing_that_has_lectures_in_each_half(config: SimulatorConfig) -> None:
    one_week = replace(config, weeks=1)
    tests = marks_schedule(one_week, KOLKATA, NOW)
    assert {t.date for t in tests} == {date(2026, 3, 16), date(2026, 3, 18)}  # the 3rd and 5th of the five lecture days
    # Lectures of five days in two blocks of three and two days: a pairing with no lecture in a block has no test.
    assert Counter(t.number for t in tests) == {1: 9, 2: 7}


# ---- marks ----------------------------------------------------------------------------------------------


def all_marks(config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]) -> list[tuple[SimTest, SimStudent, dict[str, Any]]]:
    by_register = {s.register_no: s for s in students}
    return [(t, by_register[r["register_no"]], r) for t in tests for r in marks_for(config, t, students)]


def test_marks_follow_the_archetypes(config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]) -> None:
    percent: dict[str, list[float]] = defaultdict(list)
    for _, student, row in all_marks(config, tests, students):
        percent[student.archetype].append(100 * row["marks"] / row["max_marks"])
    means = {name: mean(values) for name, values in percent.items()}
    assert means["engaged"] > 75 and means["disengaged"] < 45
    # The struggling are as attentive as anyone, but their marks sit near the bottom.
    assert means["engaged"] > means["average"] > means["struggling"]
    assert means["struggling"] < 50 and means["average"] > 55


def test_marks_are_within_the_maximum_and_only_for_the_tests_own_section(
    config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]
) -> None:
    sections = {s.register_no: s.section for s in students}
    for test in tests[:6]:
        rows = marks_for(config, test, students)
        assert rows
        assert all(0 <= r["marks"] <= test.max_marks == r["max_marks"] for r in rows)
        assert {sections[r["register_no"]] for r in rows} == {test.section}
        assert len({r["register_no"] for r in rows}) == len(rows)


def test_some_students_skip_a_test_but_most_sit_it(config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]) -> None:
    sat = sum(len(marks_for(config, t, students)) for t in tests)
    expected = 30 * len(tests)
    assert 0.65 * expected < sat < 0.95 * expected


def test_the_same_seed_gives_the_same_marks_and_another_seed_does_not(
    config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]
) -> None:
    again = marks_for(config, tests[0], students)
    assert marks_for(config, tests[0], students) == again
    other = replace(config, seed=config.seed + 1)
    assert marks_for(other, tests[0], build_students(other)) != again


def test_the_first_teachers_tests_carry_the_configured_teacher_effect_in_the_raw_marks(
    config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]
) -> None:
    sits_all = replace(config, marks=replace(config.marks, skip_chance=0.0))
    by_teacher: dict[str, list[float]] = defaultdict(list)
    for t, _, row in all_marks(sits_all, tests, students):
        by_teacher[t.teacher].append(100 * row["marks"] / row["max_marks"])
    best = config.teachers[0]
    others = mean(v for t, values in by_teacher.items() if t != best for v in values)
    assert config.marks.teacher_points == 8
    assert mean(by_teacher[best]) - others == pytest.approx(config.marks.teacher_points, abs=2.5)


def test_the_marks_figure_tends_to_favour_the_built_in_best_teacher(
    config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]
) -> None:
    """With the effect on, the best teacher's figure is the highest and clearly above zero.

    Percentiles inside a test average 50 whatever the marks, so the raw lift alone cannot show:
    it shows because fewer of the weaker students skip the best teacher's tests.
    """

    def figures(cfg: SimulatorConfig) -> dict[int, marks.MarksFigure]:
        entries = []
        for number, test in enumerate(marks_schedule(cfg, KOLKATA, NOW)):
            rows = marks_for(cfg, test, build_students(cfg))
            percentiles = marks.percentiles([marks.score(r["marks"], r["max_marks"]) for r in rows])
            entries += [
                marks.MarkEntry(r["register_no"], number, cfg.teachers.index(test.teacher), p)
                for r, p in zip(rows, percentiles, strict=True)
            ]
        return marks.marks_figures(entries, 0.95)

    on = figures(config)
    best = max(on, key=lambda t: on[t].value or 0.0)
    assert best == 0 and (on[0].value or 0) > 1.5
    # Two tests for each section the teacher teaches: Prof. Rahul Bhat (index 1) teaches only SIM-A.
    assert {teacher: figure.n_tests for teacher, figure in on.items()} == {0: 4, 1: 2, 2: 4, 3: 4, 4: 4}
    # With the effect off the first teacher has no edge.
    off = figures(replace(config, marks=replace(config.marks, teacher_points=0.0)))
    assert (off[0].value or 0) < (on[0].value or 0)


def test_the_csv_is_in_the_upload_format(config: SimulatorConfig, tests: list[SimTest], students: list[SimStudent]) -> None:
    rows = marks_for(config, tests[0], students)
    lines = marks_csv(rows).decode().splitlines()
    assert lines[0] == "register_no,marks,max_marks"
    assert len(lines) == len(rows) + 1
    register_no, mark, maximum = lines[1].split(",")
    assert register_no.startswith("SIM-") and float(mark) <= float(maximum)


# ---- a small cohort through the API ----------------------------------------------------------------------


def seed_small(client: TestClient, clock: FakeClock) -> Any:
    clock.set(NOW)
    small = replace(load_simulator_config(small=True), records_per_request=1000)
    return seed(Backend(client), small, now=NOW)


def small_groups(client: TestClient) -> list[tuple[str, str | None]]:
    """(archetype, group) for every student in every simulated test, through the API only."""
    archetype = {s["register_no"]: s["archetype"] for s in client.get("/api/students").json()}
    found: list[tuple[str, str | None]] = []
    for teacher in client.get("/api/teachers").json():
        for test in client.get(f"/api/teachers/{teacher['id']}/marks/tests").json():
            body = client.get(f"/api/marks/tests/{test['id']}/groups", params={"students": True}).json()
            found += [(archetype[s["register_no"]], s["group"]) for s in body["students"]]
    return found


def test_seeding_uploads_the_marks_through_the_endpoint_flagged_simulated(client: TestClient, clock: FakeClock) -> None:
    report = seed_small(client, clock)
    assert report.tests > 0 and report.marks > 0
    teachers = client.get("/api/teachers").json()
    listed = [t for teacher in teachers for t in client.get(f"/api/teachers/{teacher['id']}/marks/tests").json()]
    assert len(listed) == report.tests
    assert all(t["simulated"] for t in listed)
    assert sum(t["n_students"] for t in listed) == report.marks
    assert {t["name"] for t in listed} >= {"Mid-term 1 (SIM-A)", "Mid-term 2 (SIM-A)"}
    # Re-seeding replaces everything rather than adding to it, and a wipe removes it all.
    again = seed_small(client, clock)
    assert (again.tests, again.marks) == (report.tests, report.marks)
    removed = client.delete("/api/simulation").json()
    assert removed["tests"] == report.tests and removed["marks"] == report.marks
    assert all(not client.get(f"/api/teachers/{t['id']}/marks/tests").json() for t in client.get("/api/teachers").json())


def test_the_struggling_students_mostly_land_in_attentive_but_struggling(client: TestClient, clock: FakeClock) -> None:
    seed_small(client, clock)
    found = small_groups(client)
    struggling = [group for archetype, group in found if archetype == "struggling"]
    graded = [g for g in struggling if g is not None]
    assert len(graded) >= 8, "the small cohort has two struggling students and about eight tests each"
    rate = sum(g == "Attentive but struggling" for g in graded) / len(graded)
    print(f"struggling in 'Attentive but struggling': {rate:.0%} of {len(graded)}")
    assert rate >= 0.60
    # The other archetypes land where their attention and marks put them.
    by_archetype: dict[str, Counter[str | None]] = defaultdict(Counter)
    for archetype, group in found:
        by_archetype[archetype][group] += 1
    engaged = by_archetype["engaged"]
    assert engaged["On track"] > engaged["At risk"] and engaged["On track"] / sum(engaged.values()) > 0.6
    disengaged = by_archetype["disengaged"]
    assert disengaged["At risk"] > disengaged["On track"]
