"""The simulator's pure generation functions: cohort, timetable, schedule and records.

No server is involved. `NOW` is a Wednesday afternoon (UTC) so "four weeks ending today"
is the same set of days on every run.
"""

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from simulator.config import SimulatorConfig, load_simulator_config
from simulator.generate import (
    SimLecture,
    all_records,
    build_students,
    lecture_records,
    lecture_schedule,
    mean_attention,
    true_effects,
)
from simulator.timetable import DAYS, SLOT_STARTS, timetable_csv, timetable_slots

KOLKATA = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)  # Wednesday 17:30 local, after every lecture of the day


@pytest.fixture(scope="module")
def config() -> SimulatorConfig:
    return load_simulator_config()


# ---- cohort -------------------------------------------------------------------------------------


def test_the_default_cohort_is_two_sections_of_thirty_students_with_the_configured_mix(
    config: SimulatorConfig,
) -> None:
    students = build_students(config)
    assert len(students) == 60
    assert [s.register_no for s in students[:2]] == ["SIM-A01", "SIM-A02"]
    assert students[-1].register_no == "SIM-B30"
    for section in ("SIM-A", "SIM-B"):
        mix = Counter(s.archetype for s in students if s.section == section)
        assert mix == {"engaged": 10, "average": 8, "struggling": 5, "disengaged": 7}
    assert len({s.name for s in students}) == 60


def test_a_students_baseline_follows_the_archetype_and_is_the_same_on_every_run(
    config: SimulatorConfig,
) -> None:
    first, again = build_students(config), build_students(config)
    assert first == again
    by_type: dict[str, list[float]] = {}
    for s in first:
        by_type.setdefault(s.archetype, []).append(s.baseline)
    means = {name: sum(v) / len(v) for name, v in by_type.items()}
    assert means["engaged"] > means["struggling"] > means["average"] > means["disengaged"]
    assert means["disengaged"] < 0.55 and means["engaged"] > 0.78


def test_a_small_cohort_is_still_built_from_the_same_mix(config: SimulatorConfig) -> None:
    small = load_simulator_config(small=True)
    assert (small.weeks, small.students_per_section) == (1, 6)
    assert len(build_students(small)) == 12


# ---- effects and timetable --------------------------------------------------------------------------


def test_the_true_effects_name_the_teacher_the_slot_and_the_section(config: SimulatorConfig) -> None:
    effects = {(e["kind"], e["key"]): e["value"] for e in true_effects(config)}
    assert effects[("teacher", "Dr. Anita Menon")] == 6.0
    assert [v for (kind, key), v in effects.items() if kind == "teacher" and v != 0] == [6.0]
    assert effects[("slot", "14:00")] == -12.0
    assert [v for (kind, key), v in effects.items() if kind == "slot" and v != 0] == [-12.0]
    assert effects[("section", "SIM-A")] == 5.0
    assert effects[("section", "SIM-B")] == 0.0
    assert len([k for k in effects if k[0] == "teacher"]) == 5


def test_the_default_timetable_is_forty_weekly_slots_without_double_booking(
    config: SimulatorConfig,
) -> None:
    slots = timetable_slots(config)
    assert len(slots) == 40
    assert {s.room for s in slots} == {"SIM-R201", "SIM-R202"}
    seen = Counter((s.day, s.start, s.teacher) for s in slots)
    assert max(seen.values()) == 1  # a teacher is never in two rooms at once
    assert {s.start for s in slots} == set(SLOT_STARTS)


def test_the_timetable_csv_uses_the_upload_columns(config: SimulatorConfig) -> None:
    lines = timetable_csv(config).strip().splitlines()
    assert lines[0] == "day,start,end,room,section,subject,teacher"
    assert len(lines) == 41
    assert lines[1].split(",")[0] in DAYS


def test_the_timetable_is_built_to_make_the_plain_average_misleading(config: SimulatorConfig) -> None:
    slots = timetable_slots(config)
    best, flattered = config.teachers[0], config.teachers[1]
    afternoon = [s for s in slots if s.start == "14:00"]

    # The best teacher teaches mostly in the afternoon slot, and a little outside it.
    mine = [s for s in slots if s.teacher == best]
    assert sum(s.start == "14:00" for s in mine) / len(mine) >= 0.75
    assert any(s.start != "14:00" for s in mine)
    # The afternoon slot is shared by several teachers on different days.
    assert len({s.teacher for s in afternoon}) >= 4
    assert len({s.day for s in afternoon}) == 5
    # The flattered teacher has only the better section, only in the morning.
    theirs = [s for s in slots if s.teacher == flattered]
    assert {s.section for s in theirs} == {config.sections[0]}
    assert all(s.start < "12:00" for s in theirs)
    # Teachers other than those two are not confined to one slot, so each slot can be told apart.
    assert len({s.start for s in slots if s.teacher == config.teachers[2]}) == 4


# ---- schedule -------------------------------------------------------------------------------------


def test_the_schedule_covers_the_weekdays_of_the_period_ending_today(config: SimulatorConfig) -> None:
    one_week = replace(config, weeks=1)
    lectures = lecture_schedule(one_week, KOLKATA, NOW)
    # Thu 12th, Fri 13th, Mon 16th, Tue 17th and Wed 18th March, 2 sections x 4 slots each day.
    assert len(lectures) == 40
    assert lectures[0].starts_at < lectures[-1].starts_at
    first = lectures[0]
    assert first.starts_at == datetime(2026, 3, 12, 3, 30, tzinfo=UTC)  # 09:00 local
    assert first.ends_at == datetime(2026, 3, 12, 4, 20, tzinfo=UTC)
    assert len(lecture_schedule(config, KOLKATA, NOW)) == 160  # four weeks


def test_a_lecture_that_has_not_ended_yet_is_left_out(config: SimulatorConfig) -> None:
    midday = datetime(2026, 3, 18, 6, 0, tzinfo=UTC)  # 11:30 local: the 11:00 lectures are running
    lectures = lecture_schedule(replace(config, weeks=1), KOLKATA, midday)
    today = [x for x in lectures if x.date.isoformat() == "2026-03-18"]
    assert sorted({x.slot.start for x in today}) == ["09:00", "10:00"]
    assert len(lectures) == 36


# ---- records -------------------------------------------------------------------------------------------


def one_lecture(config: SimulatorConfig, start: str, section: str, day: str = "Wed") -> SimLecture:
    return next(
        x
        for x in lecture_schedule(replace(config, weeks=1), KOLKATA, NOW)
        if x.slot.start == start and x.slot.section == section and DAYS[x.date.weekday()] == day
    )


def test_each_record_is_a_whole_window_of_seconds_for_one_student(config: SimulatorConfig) -> None:
    students = [s for s in build_students(config) if s.section == "SIM-A"]
    lecture = one_lecture(config, "10:00", "SIM-A")
    records = lecture_records(config, lecture, students)
    assert len(records) <= 30 * 100
    assert len(records) > 25 * 100  # a few students are absent
    first = records[0]
    assert set(first) == {
        "camera_id",
        "student_id",
        "window_start",
        "attentive",
        "neutral",
        "distracted",
        "not_visible",
    }
    assert first["camera_id"] == "SIM-CAM-201"
    for r in records:
        assert r["attentive"] + r["neutral"] + r["distracted"] + r["not_visible"] == 30
    # 10:00 local on Wednesday 18th is 04:30 UTC; windows run every 30 seconds.
    starts = sorted({r["window_start"] for r in records})
    assert starts[0] == "2026-03-18T04:30:00+00:00"
    assert starts[1] == "2026-03-18T04:30:30+00:00"
    assert len(starts) == 100


def test_some_students_miss_whole_lectures_and_some_windows_are_not_visible(
    config: SimulatorConfig,
) -> None:
    students = build_students(config)
    lectures = lecture_schedule(replace(config, weeks=1), KOLKATA, NOW)[:8]
    attended = 0
    possible = 0
    hidden = neutral = total = 0.0
    for lecture in lectures:
        section_students = [s for s in students if s.section == lecture.slot.section]
        records = lecture_records(config, lecture, section_students)
        attended += len({r["student_id"] for r in records})
        possible += len(section_students)
        hidden += sum(r["not_visible"] for r in records)
        neutral += sum(r["neutral"] for r in records)
        total += 30 * len(records)
    assert 0.80 < attended / possible < 0.97  # attendance is not 100%
    assert 0.01 < hidden / total < 0.20
    assert 0.03 < neutral / total < 0.15


def test_the_same_seed_gives_the_same_records_and_another_seed_does_not(
    config: SimulatorConfig,
) -> None:
    students = [s for s in build_students(config) if s.section == "SIM-A"]
    lecture = one_lecture(config, "11:00", "SIM-A")
    again = lecture_records(config, lecture, students)
    assert lecture_records(config, lecture, students) == again
    other = replace(config, seed=config.seed + 1)
    assert lecture_records(other, lecture, build_students(other)) != again


def test_the_mean_attention_is_baseline_plus_section_teacher_and_slot_effects(
    config: SimulatorConfig,
) -> None:
    student = next(s for s in build_students(config) if s.section == "SIM-A")
    best_afternoon = one_lecture(config, "14:00", "SIM-A", "Mon")  # Dr. Anita Menon
    assert best_afternoon.slot.teacher == "Dr. Anita Menon"
    assert mean_attention(config, student, best_afternoon) == pytest.approx(
        student.baseline + 0.06 - 0.12 + 0.05
    )
    flattered_morning = one_lecture(config, "09:00", "SIM-A", "Mon")  # Prof. Rahul Bhat
    assert mean_attention(config, student, flattered_morning) == pytest.approx(
        student.baseline + 0.05
    )
    other_section = next(s for s in build_students(config) if s.section == "SIM-B")
    neutral = one_lecture(config, "10:00", "SIM-B", "Mon")
    assert mean_attention(config, other_section, neutral) == pytest.approx(other_section.baseline)


# ---- the plain average misleads -----------------------------------------------------------------------


def test_the_plain_class_average_orders_the_teachers_differently_from_their_true_effects(
    config: SimulatorConfig,
) -> None:
    """Averaging attention over each teacher's lectures, as a naive report would, gets the order wrong.

    The better teacher mostly teaches the worse slot and loses to a neutral teacher who has the
    more attentive section in the mornings. (The self-comparison and slot-adjusted figures that
    undo this are computed in CLA-13.)
    """
    small = replace(config, students_per_section=10, weeks=2)
    attentive: Counter[str] = Counter()
    distracted: Counter[str] = Counter()
    for lecture, records in all_records(small, KOLKATA, NOW):
        for r in records:
            attentive[lecture.slot.teacher] += r["attentive"]
            distracted[lecture.slot.teacher] += r["distracted"]
    plain = {t: attentive[t] / (attentive[t] + distracted[t]) for t in small.teachers}

    effects = {e["key"]: e["value"] for e in true_effects(small) if e["kind"] == "teacher"}
    true_best = max(effects, key=lambda t: effects[t])
    assert true_best == "Dr. Anita Menon"
    plain_order = sorted(plain, key=lambda t: plain[t], reverse=True)
    true_order = sorted(effects, key=lambda t: effects[t], reverse=True)
    assert plain_order[0] != true_best  # the best teacher does not look best
    assert plain_order[0] == "Prof. Rahul Bhat"  # a neutral teacher with the better section does
    assert plain_order.index(true_best) >= 3  # and the best teacher looks among the worst
    assert plain_order != true_order
    # Not a rounding accident: the neutral teacher leads the best one by several points.
    assert plain["Prof. Rahul Bhat"] - plain[true_best] > 0.04
