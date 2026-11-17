"""Marks, as plain functions against hand-worked numbers (backend/marks.py): percentiles, which
lectures a test covers, the four student groups and the marks-based teacher figure.

Nothing here touches the database or the API.
"""

from datetime import date
from statistics import NormalDist

import pytest

from backend import marks
from backend.marks import (
    AT_RISK,
    ATTENTIVE_BUT_STRUGGLING,
    KNOWS_IT,
    ON_TRACK,
    LectureRef,
    MarkEntry,
    MarkResult,
    OtherTest,
)
from backend.metrics import LOW_PRESENCE, NO_RECORDS, Seconds

# ---- percentiles ---------------------------------------------------------------------------------


def test_a_percentile_is_the_share_below_plus_half_the_share_equal() -> None:
    # 50 has nobody below and itself equal: (0 + 0.5) / 4. 60 and 60 have one below and two equal:
    # (1 + 1) / 4. 80 has three below and itself: (3 + 0.5) / 4.
    assert marks.percentiles([50, 60, 60, 80]) == [12.5, 50.0, 50.0, 87.5]


def test_percentiles_keep_the_order_they_were_given_in() -> None:
    assert marks.percentiles([80, 50, 60, 60]) == [87.5, 12.5, 50.0, 50.0]


def test_one_student_is_at_the_middle() -> None:
    assert marks.percentiles([37.0]) == [50.0]


def test_everybody_tied_is_everybody_at_the_middle() -> None:
    assert marks.percentiles([7, 7, 7, 7]) == [50.0] * 4


def test_distinct_marks_of_five_students_are_ten_thirty_fifty_seventy_ninety() -> None:
    assert marks.percentiles([1, 2, 3, 4, 5]) == [10.0, 30.0, 50.0, 70.0, 90.0]


def test_the_percentiles_of_any_class_average_fifty() -> None:
    scores = [12, 40, 40, 41, 41, 41, 90, 3]
    assert sum(marks.percentiles(scores)) / len(scores) == pytest.approx(50.0)


def test_no_marks_give_no_percentiles() -> None:
    assert marks.percentiles([]) == []


# ---- which lectures a test covers ---------------------------------------------------------------

TEACHER, OTHER_TEACHER = 1, 2
ALGO, DB = 10, 11
SEC_A, SEC_B = 100, 101


def lecture(lecture_id: int, day: str, *, teacher: int = TEACHER, subject: int = ALGO, section: int | None = SEC_A) -> LectureRef:
    return LectureRef(lecture_id, teacher, subject, section, date.fromisoformat(day))


LECTURES = [
    lecture(1, "2026-03-02"),
    lecture(2, "2026-03-09"),  # the day of the first test
    lecture(3, "2026-03-10"),
    lecture(4, "2026-03-23"),  # the day of the second test
    lecture(5, "2026-03-24"),  # after the second test
    lecture(6, "2026-03-03", teacher=OTHER_TEACHER),  # another teacher, same subject
    lecture(7, "2026-03-03", subject=DB),  # another subject
    lecture(8, "2026-03-03", section=SEC_B),  # another section
    lecture(9, "2026-03-04", section=None),  # a manual session with no section
]


def test_the_first_test_covers_every_lecture_from_the_beginning_up_to_its_date() -> None:
    covered = marks.covered_lectures(
        LECTURES, teacher=TEACHER, subject=ALGO, test_date=date(2026, 3, 9), previous={SEC_A: None}
    )
    assert covered == [1, 2]  # not 3 (after the test), not 6, 7, 8 or 9 (other teacher, subject, section)


def test_the_next_test_covers_the_lectures_after_the_previous_one_up_to_its_date() -> None:
    covered = marks.covered_lectures(
        LECTURES,
        teacher=TEACHER,
        subject=ALGO,
        test_date=date(2026, 3, 23),
        previous={SEC_A: date(2026, 3, 9)},
    )
    assert covered == [3, 4]  # lecture 2 was on the previous test's day, so it belongs to that test


def test_a_test_for_two_sections_covers_each_from_its_own_previous_test() -> None:
    covered = marks.covered_lectures(
        LECTURES,
        teacher=TEACHER,
        subject=ALGO,
        test_date=date(2026, 3, 23),
        previous={SEC_A: date(2026, 3, 9), SEC_B: None},
    )
    assert covered == [3, 4, 8]


def test_a_test_with_no_sections_covers_nothing() -> None:
    assert marks.covered_lectures(LECTURES, teacher=TEACHER, subject=ALGO, test_date=date(2026, 3, 23), previous={}) == []


def test_the_previous_test_is_the_latest_earlier_one_of_the_same_sections() -> None:
    others = [
        OtherTest(date(2026, 3, 2), frozenset({SEC_A})),
        OtherTest(date(2026, 3, 9), frozenset({SEC_B})),
        OtherTest(date(2026, 3, 16), frozenset({SEC_A, SEC_B})),
        OtherTest(date(2026, 3, 23), frozenset({SEC_A})),  # the same day: a sibling, not a previous test
        OtherTest(date(2026, 3, 30), frozenset({SEC_A})),  # later
    ]
    both = marks.previous_dates(date(2026, 3, 23), others, {SEC_A, SEC_B})
    assert both == {SEC_A: date(2026, 3, 16), SEC_B: date(2026, 3, 16)}
    earlier = marks.previous_dates(date(2026, 3, 12), others, {SEC_A, SEC_B})
    assert earlier == {SEC_A: date(2026, 3, 2), SEC_B: date(2026, 3, 9)}
    first = marks.previous_dates(date(2026, 3, 1), others, {SEC_A})
    assert first == {SEC_A: None}


# ---- the four groups ---------------------------------------------------------------------------

LINE = 0.60
MIN_PRESENCE = 0.5


def in_view(attentive: float, distracted: float) -> Seconds:
    return Seconds(attentive=attentive, distracted=distracted)


def group_of(results: list[MarkResult], seconds: dict[str, Seconds]) -> dict[str, str | None]:
    grouping = marks.assign_groups(results, seconds, MIN_PRESENCE, LINE)
    return {s.student: s.group for s in grouping.students}


def test_marks_above_the_median_and_attention_above_the_line_is_on_track() -> None:
    results = [MarkResult("a", 0.4), MarkResult("b", 0.5), MarkResult("c", 0.9)]
    seconds = {"a": in_view(30, 70), "b": in_view(30, 70), "c": in_view(80, 20)}
    groups = group_of(results, seconds)
    assert groups["c"] == ON_TRACK  # 0.9 is above the median 0.5, attention 0.8 is above 0.6


def test_the_four_cells_of_the_grid() -> None:
    # Marks: median is 0.55, the average of the two middle scores 0.5 and 0.6.
    results = [MarkResult("high1", 0.8), MarkResult("high2", 0.6), MarkResult("low1", 0.5), MarkResult("low2", 0.3)]
    seconds = {
        "high1": in_view(80, 20),  # attention 0.8
        "high2": in_view(40, 60),  # attention 0.4
        "low1": in_view(70, 30),  # attention 0.7
        "low2": in_view(20, 80),  # attention 0.2
    }
    grouping = marks.assign_groups(results, seconds, MIN_PRESENCE, LINE)
    assert grouping.median == pytest.approx(0.55)
    assert {s.student: s.group for s in grouping.students} == {
        "high1": ON_TRACK,
        "high2": KNOWS_IT,  # marks above the median, attention at or below the line
        "low1": ATTENTIVE_BUT_STRUGGLING,  # attention above the line, marks at or below the median
        "low2": AT_RISK,
    }


def test_marks_exactly_at_the_median_count_as_at_or_below() -> None:
    results = [MarkResult("a", 0.4), MarkResult("b", 0.6), MarkResult("c", 0.8)]  # median 0.6
    seconds = {s: in_view(90, 10) for s in "abc"}
    groups = group_of(results, seconds)
    assert groups == {"a": ATTENTIVE_BUT_STRUGGLING, "b": ATTENTIVE_BUT_STRUGGLING, "c": ON_TRACK}


def test_attention_of_exactly_sixty_percent_is_at_or_below_the_line() -> None:
    results = [MarkResult("a", 0.2), MarkResult("b", 0.9)]  # median 0.55: a is below, b above
    seconds = {"a": in_view(60, 40), "b": in_view(60, 40)}  # attention exactly 0.60
    assert group_of(results, seconds) == {"a": AT_RISK, "b": KNOWS_IT}
    just_above = {"a": in_view(61, 39), "b": in_view(61, 39)}
    assert group_of(results, just_above) == {"a": ATTENTIVE_BUT_STRUGGLING, "b": ON_TRACK}


def test_scores_are_marks_over_the_maximum_so_tests_out_of_50_and_100_compare() -> None:
    assert marks.score(45, 50) == pytest.approx(0.9)
    assert marks.score(45, 100) == pytest.approx(0.45)


def test_a_student_in_view_for_less_than_half_the_time_is_insufficient_data_not_grouped() -> None:
    results = [MarkResult("a", 0.2), MarkResult("b", 0.9), MarkResult("c", 0.5)]
    seconds = {
        "a": Seconds(attentive=10, distracted=5, not_visible=85),  # in view 15 of 100 seconds
        "b": in_view(80, 20),
        # c has no records at all
    }
    grouping = marks.assign_groups(results, seconds, MIN_PRESENCE, LINE)
    by_student = {s.student: s for s in grouping.students}
    assert by_student["a"].group is None and by_student["a"].reason == LOW_PRESENCE
    assert by_student["a"].attention is None
    assert by_student["c"].group is None and by_student["c"].reason == NO_RECORDS
    assert by_student["b"].group == ON_TRACK
    # The median still counts everybody's marks, grouped or not: 0.2, 0.5, 0.9.
    assert grouping.median == pytest.approx(0.5)


def test_presence_exactly_at_the_threshold_passes_the_gate() -> None:
    results = [MarkResult("a", 0.5)]
    seconds = {"a": Seconds(attentive=40, distracted=10, not_visible=50)}  # presence 0.5, attention 0.8
    assert marks.assign_groups(results, seconds, MIN_PRESENCE, LINE).students[0].group == ATTENTIVE_BUT_STRUGGLING


def test_attention_is_worked_from_summed_seconds_and_gets_a_percentile_among_those_with_a_value() -> None:
    results = [MarkResult(s, 0.5) for s in ("a", "b", "c", "d")]
    seconds = {
        "a": in_view(50, 50),  # 0.5
        "b": in_view(70, 30),  # 0.7
        "c": in_view(70, 30),  # 0.7
        "d": Seconds(attentive=1, not_visible=99),  # insufficient
    }
    grouping = marks.assign_groups(results, seconds, MIN_PRESENCE, LINE)
    by_student = {s.student: s for s in grouping.students}
    assert by_student["b"].attention == pytest.approx(0.7)
    # Three students with a value: 0.5 is lowest (0.5 / 3), the two 0.7 tie ((1 + 1) / 3).
    assert by_student["a"].attention_percentile == pytest.approx(100 / 6)
    assert by_student["b"].attention_percentile == pytest.approx(100 * 2 / 3)
    assert by_student["d"].attention_percentile is None


def test_each_student_carries_their_percentile_in_the_test() -> None:
    results = [MarkResult("a", 0.5), MarkResult("b", 0.6), MarkResult("c", 0.6), MarkResult("d", 0.8)]
    grouping = marks.assign_groups(results, {}, MIN_PRESENCE, LINE)
    assert [s.marks_percentile for s in grouping.students] == [12.5, 50.0, 50.0, 87.5]


def test_the_groups_of_an_empty_test_are_empty() -> None:
    grouping = marks.assign_groups([], {}, MIN_PRESENCE, LINE)
    assert grouping.students == [] and grouping.median is None


# ---- the marks-based teacher figure ----------------------------------------------------------------


def entry(student: str, test: int, teacher: int, percentile: float) -> MarkEntry:
    return MarkEntry(student, test, teacher, percentile)


def test_a_teachers_figure_is_the_class_mean_of_own_percentile_minus_the_all_subjects_average() -> None:
    entries = [
        entry("s1", 1, TEACHER, 80), entry("s2", 1, TEACHER, 40), entry("s3", 1, TEACHER, 60),
        entry("s1", 2, OTHER_TEACHER, 50), entry("s2", 2, OTHER_TEACHER, 60), entry("s3", 2, OTHER_TEACHER, 10),
    ]  # fmt: skip
    # Teacher 1: s1 80 - (80 + 50) / 2 = +15, s2 40 - (40 + 60) / 2 = -10, s3 60 - (60 + 10) / 2 = +25.
    figures = marks.marks_figures(entries, 0.95)
    one = figures[TEACHER]
    assert one.value == pytest.approx(10.0)
    assert one.n_students == 3 and one.n_tests == 1
    # The sample sd of 15, -10, 25 is sqrt(650 / 2) = 18.0278; half range = z x sd / sqrt(3).
    half = NormalDist().inv_cdf(0.975) * (325**0.5) / 3**0.5
    assert one.low == pytest.approx(10.0 - half) and one.high == pytest.approx(10.0 + half)
    # Teacher 2: s1 50 - 65 = -15, s2 60 - 50 = +10, s3 10 - 35 = -25, mean -10.
    assert figures[OTHER_TEACHER].value == pytest.approx(-10.0)


def test_with_two_tests_a_student_counts_once_with_the_mean_of_their_percentiles_in_the_teachers_tests() -> None:
    entries = [
        entry("s1", 1, TEACHER, 80), entry("s1", 2, TEACHER, 60), entry("s1", 3, OTHER_TEACHER, 50),
        entry("s2", 1, TEACHER, 20), entry("s2", 2, TEACHER, 40), entry("s2", 3, OTHER_TEACHER, 90),
    ]  # fmt: skip
    # s1: mean of 80 and 60 is 70, over all three tests (80 + 60 + 50) / 3 = 63.33, so +6.67.
    # s2: mean of 20 and 40 is 30, over all three (20 + 40 + 90) / 3 = 50, so -20.
    one = marks.marks_figures(entries, 0.95)[TEACHER]
    assert one.value == pytest.approx((70 - 190 / 3 + 30 - 50) / 2)
    assert one.n_students == 2 and one.n_tests == 2


def test_the_figure_cannot_be_anything_but_zero_when_every_student_sits_every_test() -> None:
    # Mid-rank percentiles of a class average 50 in each test, so with the same students in every
    # test the class mean of own minus all-subjects average cancels exactly. Only who sits which
    # test (and composition) can move it; the docs say so.
    entries = []
    for test, teacher, scores in ((1, TEACHER, [3, 9, 4, 4]), (2, OTHER_TEACHER, [70, 20, 30, 80])):
        for student, percentile in zip("wxyz", marks.percentiles(scores), strict=True):
            entries.append(entry(student, test, teacher, percentile))
    figures = marks.marks_figures(entries, 0.95)
    assert figures[TEACHER].value == pytest.approx(0.0, abs=1e-9)
    assert figures[OTHER_TEACHER].value == pytest.approx(0.0, abs=1e-9)


def test_a_student_with_only_this_teachers_tests_adds_a_zero() -> None:
    entries = [
        entry("s1", 1, TEACHER, 80), entry("s1", 2, OTHER_TEACHER, 60),  # 80 - 70 = +10
        entry("s2", 1, TEACHER, 20),  # only this teacher's test: 20 - 20 = 0
    ]  # fmt: skip
    one = marks.marks_figures(entries, 0.95)[TEACHER]
    assert one.value == pytest.approx(5.0) and one.n_students == 2


def test_one_student_gives_a_value_and_no_range() -> None:
    entries = [entry("s1", 1, TEACHER, 80), entry("s1", 2, OTHER_TEACHER, 60)]
    one = marks.marks_figures(entries, 0.95)[TEACHER]
    assert one.value == pytest.approx(10.0)
    assert one.low is None and one.high is None and one.n_students == 1


def test_a_teacher_with_no_tests_has_no_figure() -> None:
    figures = marks.marks_figures([entry("s1", 1, TEACHER, 80)], 0.95)
    assert set(figures) == {TEACHER}
    assert marks.marks_figures([], 0.95) == {}
