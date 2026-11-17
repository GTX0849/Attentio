"""The teacher figures as plain functions, checked against numbers worked out by hand.

Attention is written in percent per student and lecture: `cell(student, lecture, teacher, slot, 80)`
is 80 attentive and 20 distracted seconds, so 80%. Each cell has 100 seconds in view and none
hidden, so every cell passes the visibility gate unless a test says otherwise.
"""

import pytest

from backend import teacher_figures as tf
from backend.metrics import Seconds
from backend.teacher_figures import StudentLecture, TeacherSettings

MIN_PRESENCE = 0.5
SETTINGS = TeacherSettings(min_lectures=1, range_level=0.95)


def cell(student: str, lecture: int | None, teacher: int | None, slot: str | None, pct: float) -> StudentLecture:
    return StudentLecture(student, lecture, teacher, slot, Seconds(attentive=pct, distracted=100 - pct))


# Two students, four lectures, everyone at every lecture.
#
#            L1 (T1, 10:00)  L2 (T1, 10:00)  L3 (T2, 10:00)  L4 (T2, 14:00)   own average
#   s1             80              70              60              50          260/400 = 65
#   s2             90              80              70              40          280/400 = 70
#
# Difference from their own average, in points:
#   s1  +15  +5  -5  -15
#   s2  +20  +10  0  -30
COHORT = [
    cell("s1", 1, 1, "10:00", 80),
    cell("s1", 2, 1, "10:00", 70),
    cell("s1", 3, 2, "10:00", 60),
    cell("s1", 4, 2, "14:00", 50),
    cell("s2", 1, 1, "10:00", 90),
    cell("s2", 2, 1, "10:00", 80),
    cell("s2", 3, 2, "10:00", 70),
    cell("s2", 4, 2, "14:00", 40),
]


def test_each_student_lecture_is_compared_with_the_students_own_average_in_points() -> None:
    d = {(x.student, x.lecture): x.points for x in tf.deltas(COHORT, MIN_PRESENCE)}
    assert d[("s1", 1)] == pytest.approx(15)
    assert d[("s1", 4)] == pytest.approx(-15)
    assert d[("s2", 1)] == pytest.approx(20)
    assert d[("s2", 4)] == pytest.approx(-30)
    assert len(d) == 8


def test_the_students_own_average_is_worked_from_summed_seconds_not_averaged_percentages() -> None:
    # One student, two lectures. L1: 30 attentive, 0 distracted (100%). L2: 30 attentive, 90 distracted (25%).
    # Summed: 60 / (60 + 90) = 40%, not the 62.5% mean of 100% and 25%.
    rows = [
        StudentLecture("s", 1, 1, "10:00", Seconds(attentive=30)),
        StudentLecture("s", 2, 2, "11:00", Seconds(attentive=30, distracted=90)),
    ]
    d = {x.lecture: x.points for x in tf.deltas(rows, MIN_PRESENCE)}
    assert d[1] == pytest.approx(60)  # 100 - 40
    assert d[2] == pytest.approx(-15)  # 25 - 40


def test_a_slot_effect_is_the_mean_difference_over_every_student_and_lecture_in_that_slot() -> None:
    effects = tf.slot_effects(tf.deltas(COHORT, MIN_PRESENCE))
    # 10:00: (15 + 5 - 5 + 20 + 10 + 0) / 6 = 7.5.   14:00: (-15 - 30) / 2 = -22.5
    assert effects == {"10:00": pytest.approx(7.5), "14:00": pytest.approx(-22.5)}


def test_self_comparison_is_the_mean_over_students_of_their_mean_difference_in_that_teachers_lectures() -> None:
    figures = tf.teacher_figures(COHORT, SETTINGS, MIN_PRESENCE)
    one, two = figures[1], figures[2]
    # T1: s1 (15 + 5) / 2 = 10, s2 (20 + 10) / 2 = 15 -> 12.5.   T2: s1 -10, s2 -15 -> -12.5
    assert one.self_comparison.value == pytest.approx(12.5)
    assert two.self_comparison.value == pytest.approx(-12.5)
    assert one.self_comparison.n_students == 2
    assert (one.n_lectures, two.n_lectures) == (2, 2)
    assert not one.hidden and one.reason is None


def test_slot_adjusted_subtracts_the_slot_effect_from_every_difference_first() -> None:
    figures = tf.teacher_figures(COHORT, SETTINGS, MIN_PRESENCE)
    # T1: s1 (15 - 7.5, 5 - 7.5) -> mean 2.5; s2 (20 - 7.5, 10 - 7.5) -> mean 7.5; figure 5.0
    # T2: s1 (-5 - 7.5, -15 + 22.5) -> mean -2.5; s2 (0 - 7.5, -30 + 22.5) -> mean -7.5; figure -5.0
    assert figures[1].slot_adjusted.value == pytest.approx(5.0)
    assert figures[2].slot_adjusted.value == pytest.approx(-5.0)


def test_the_range_is_mean_plus_or_minus_1_96_standard_errors_across_students() -> None:
    one = tf.teacher_figures(COHORT, SETTINGS, MIN_PRESENCE)[1]
    # Student means 10 and 15: sd = sqrt(((10 - 12.5)^2 + (15 - 12.5)^2) / 1) = 3.5355, se = sd / sqrt(2) = 2.5,
    # half width 1.96 * 2.5 = 4.9
    assert one.self_comparison.low == pytest.approx(7.6, abs=0.01)
    assert one.self_comparison.high == pytest.approx(17.4, abs=0.01)
    # Slot-adjusted student means 2.5 and 7.5: same spread around 5.0
    assert one.slot_adjusted.low == pytest.approx(0.1, abs=0.01)
    assert one.slot_adjusted.high == pytest.approx(9.9, abs=0.01)


def test_the_range_level_comes_from_the_settings() -> None:
    wide = TeacherSettings(min_lectures=1, range_level=0.99)  # z = 2.576
    one = tf.teacher_figures(COHORT, wide, MIN_PRESENCE)[1]
    assert one.self_comparison.high == pytest.approx(12.5 + 2.5758 * 2.5, abs=0.01)


def test_one_student_gives_a_figure_but_no_range() -> None:
    rows = [cell("s", 1, 1, "10:00", 80), cell("s", 2, 2, "10:00", 60)]
    one = tf.teacher_figures(rows, SETTINGS, MIN_PRESENCE)[1]
    assert one.self_comparison.value == pytest.approx(10)  # 80 against their own 70
    assert one.self_comparison.n_students == 1
    assert one.self_comparison.low is None and one.self_comparison.high is None


def test_figures_stay_hidden_below_the_minimum_number_of_lectures() -> None:
    five = TeacherSettings(min_lectures=5, range_level=0.95)
    # Teacher 1 has 4 lectures with records, teacher 2 has exactly 5, with 3 students in each lecture.
    rows = [
        cell(s, lecture, 1, "10:00", 70 + lecture) for s in ("a", "b", "c") for lecture in range(1, 5)
    ] + [
        cell(s, lecture, 2, "11:00", 60 + lecture) for s in ("a", "b", "c") for lecture in range(11, 16)
    ]
    figures = tf.teacher_figures(rows, five, MIN_PRESENCE)
    few = figures[1]
    assert few.n_lectures == 4
    assert few.hidden and few.reason == "fewer than 5 lectures"
    assert few.self_comparison.value is None and few.self_comparison.low is None
    assert few.slot_adjusted.value is None
    assert few.self_comparison.n_students == 0

    enough = figures[2]
    assert enough.n_lectures == 5
    assert not enough.hidden and enough.reason is None
    assert enough.self_comparison.value is not None


def test_a_lecture_counts_toward_the_minimum_when_it_has_any_records_even_if_nobody_passes_the_gate() -> None:
    five = TeacherSettings(min_lectures=5, range_level=0.95)
    barely = Seconds(attentive=5, not_visible=95)  # presence 5%, under the gate
    rows = [StudentLecture("s", n, 1, "10:00", barely) for n in range(1, 6)]
    one = tf.teacher_figures(rows, five, MIN_PRESENCE)[1]
    assert one.n_lectures == 5 and not one.hidden
    assert one.self_comparison.value is None and one.self_comparison.n_students == 0


def test_a_student_lecture_in_view_less_than_the_threshold_is_left_out_everywhere() -> None:
    # s1's L3 is 5% in view: it adds nothing to their own average and gives no difference of its own.
    rows = [
        cell("s1", 1, 1, "10:00", 80),
        cell("s1", 2, 2, "10:00", 60),
        StudentLecture("s1", 3, 1, "14:00", Seconds(attentive=5, not_visible=95)),
    ]
    d = {x.lecture: x.points for x in tf.deltas(rows, MIN_PRESENCE)}
    assert set(d) == {1, 2}
    assert d[1] == pytest.approx(10)  # 80 against (80 + 60) / 2 = 70


def test_records_with_no_lecture_are_ignored_throughout() -> None:
    clean = tf.teacher_figures(COHORT, SETTINGS, MIN_PRESENCE)
    unassigned = [
        cell("s1", None, None, None, 0),  # a terrible unassigned window for s1
        cell("s2", None, None, None, 100),
    ]
    noisy = tf.teacher_figures(COHORT + unassigned, SETTINGS, MIN_PRESENCE)
    assert set(noisy) == {1, 2}
    assert noisy[1].self_comparison.value == pytest.approx(clean[1].self_comparison.value or 0)
    assert noisy[1].slot_adjusted.value == pytest.approx(clean[1].slot_adjusted.value or 0)
    assert noisy[1].n_lectures == 2
    assert len(tf.deltas(COHORT + unassigned, MIN_PRESENCE)) == 8


def test_two_rows_for_the_same_student_and_lecture_are_summed() -> None:
    split = [
        StudentLecture("s", 1, 1, "10:00", Seconds(attentive=40, distracted=10)),
        StudentLecture("s", 1, 1, "10:00", Seconds(attentive=40, distracted=10)),
        cell("s", 2, 2, "10:00", 60),
    ]
    d = {x.lecture: x.points for x in tf.deltas(split, MIN_PRESENCE)}
    assert d[1] == pytest.approx(80 - 70)  # 80/100 against (80 + 60) / (100 + 100) = 70%


def test_a_slot_always_taught_by_one_teacher_is_removed_entirely_from_that_teacher() -> None:
    # The honest limit: T1 only ever teaches at 10:00, T2 only at 14:00. The slot effect equals the teacher effect.
    rows = [
        cell("s1", 1, 1, "10:00", 80),
        cell("s1", 2, 2, "14:00", 60),
        cell("s2", 1, 1, "10:00", 90),
        cell("s2", 2, 2, "14:00", 70),
    ]
    figures = tf.teacher_figures(rows, SETTINGS, MIN_PRESENCE)
    assert figures[1].self_comparison.value == pytest.approx(10)
    assert figures[2].self_comparison.value == pytest.approx(-10)
    assert figures[1].slot_adjusted.value == pytest.approx(0)
    assert figures[2].slot_adjusted.value == pytest.approx(0)


# ---- a cohort with only a section effect ------------------------------------------------------------


def section_effect_rows() -> list[StudentLecture]:
    """Section A is 20 points more attentive than section B. Teacher X is truly +5, teacher Y is 0.

    X teaches one lecture of A and three of B; Y teaches three of A and one of B, all in the same
    slot. Every student attends every lecture of their section and has four lectures in all.
      A: X 85, Y 80 each        B: X 65, Y 60 each
    """
    rows: list[StudentLecture] = []
    plan = {
        "A": [(1, 1, 85), (2, 2, 80), (3, 2, 80), (4, 2, 80)],  # (lecture id, teacher, attention)
        "B": [(5, 1, 65), (6, 1, 65), (7, 1, 65), (8, 2, 60)],
    }
    for section, lectures in plan.items():
        for student in (f"{section}1", f"{section}2"):
            rows += [cell(student, lecture, teacher, "10:00", pct) for lecture, teacher, pct in lectures]
    return rows


def test_a_plain_class_average_mis_orders_two_teachers_and_the_self_comparison_recovers_the_order() -> None:
    rows = section_effect_rows()

    def plain_average(teacher: int) -> float:
        parts = [x.seconds for x in rows if x.teacher == teacher]
        return 100 * sum(p.attentive for p in parts) / sum(p.attentive + p.distracted for p in parts)

    # X: (1 lecture x 85 + 3 x 65) / 4 = 70.   Y: (3 x 80 + 1 x 60) / 4 = 75. The plain average puts Y first.
    assert plain_average(1) == pytest.approx(70)
    assert plain_average(2) == pytest.approx(75)
    assert plain_average(2) > plain_average(1)

    figures = tf.teacher_figures(rows, SETTINGS, MIN_PRESENCE)
    # A students average (85 + 240) / 4 = 81.25: X +3.75, Y -1.25. B students average 63.75: X +1.25, Y -3.75.
    assert figures[1].self_comparison.value == pytest.approx(2.5)  # (3.75 + 1.25) / 2
    assert figures[2].self_comparison.value == pytest.approx(-2.5)
    assert (figures[1].self_comparison.value or 0) > (figures[2].self_comparison.value or 0)


def test_the_settings_are_read_from_the_teachers_section_of_the_config() -> None:
    settings = tf.teacher_settings()
    assert settings.min_lectures == 5
    assert settings.range_level == pytest.approx(0.95)
