"""Marks: percentiles, the lectures a test covers, the four student groups and the marks figure.

Plain functions; nothing here reads the database (see `backend.marks_service` for that).

Definitions
-----------
Score
    marks / max_marks, so tests out of 50 and out of 100 compare.

Percentile (within one test)
    The mid-rank percentile, 0 to 100: for each student, 100 x (students with a lower score + half
    the students with the same score, counting the student themselves) / students in the test.
    Tied students share one value, the mean of the ranks they span. Distinct scores of five
    students give 10, 30, 50, 70, 90; one student gives 50. The percentiles of a test always
    average exactly 50.

Covered lectures
    A test covers every lecture of its subject taught by its teacher to the sections of the
    students in the test, dated after the previous test of the same teacher and subject for that
    section (or from the beginning when there is none) and up to the test date. Lecture dates are
    local calendar dates, and a lecture on the day of a test counts for that test. The previous
    test of a section is the latest test of the same teacher and subject with an earlier date that
    has a student of that section; a test on the same date is a sibling, not a previous test.
    Lectures with no section (manual sessions) are not covered.

Student groups (for one test)
    Attention is attentive / (attentive + distracted) over the student's summed seconds in the
    covered lectures, with the usual visibility gate: a student in view for less than
    `scores.min_presence_ratio` of the recorded time (or with no records) is "insufficient data"
    and is not grouped. Attention "above 60%" means the attention value is strictly above the
    `marks.attention_line` of the config (0.60); exactly 60% is at or below. "Marks above the class
    median" means the score is strictly above the median score of everybody in the test (also those
    with insufficient attention data); a score equal to the median is at or below.

        attention above the line, marks above the median      On track
        attention above the line, marks at or below           Attentive but struggling
        attention at or below the line, marks above           Knows it already
        attention at or below the line, marks at or below     At risk

    Each student also gets an attention percentile among the students that have an attention
    value, for display only; the groups use the attention value itself.

Marks figure for a teacher
    For each student with marks in the teacher's tests: the mean of their percentiles in the
    teacher's tests, minus the mean of their percentiles in ALL tests they sat (every teacher and
    subject, the teacher's own included). The figure is the mean of that over those students,
    with the 95% range of `figure_with_range` and the number of tests.

    What this can and cannot do. Percentiles are relative inside a test, so the percentiles of a
    test average 50 whatever the marks are. If every student sat every test the figure is exactly
    zero, and a uniform lift in one teacher's marks changes nothing. The figure moves only when
    the students of one teacher's tests are not the students of the others: when different
    students sit different tests (absence, choosing to skip), or when a teacher's tests are taken
    by a different mix of students than the rest. It is feedback on how a teacher's students rank
    against their own usual place, not a measure of how much they learned.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import mean, median

from backend import metrics, teacher_figures
from backend.metrics import Seconds
from shared.config import load_config

ON_TRACK = "On track"
ATTENTIVE_BUT_STRUGGLING = "Attentive but struggling"
KNOWS_IT = "Knows it already"
AT_RISK = "At risk"
# In display order: attention above the line first, marks above the median first.
GROUP_ORDER = [ON_TRACK, ATTENTIVE_BUT_STRUGGLING, KNOWS_IT, AT_RISK]

NO_TESTS = "no marks uploaded"


@dataclass(frozen=True)
class MarksSettings:
    attention_line: float  # attention strictly above this is "attentive" (0 to 1)


def marks_settings() -> MarksSettings:
    """The `marks:` section of the shared config."""
    return MarksSettings(attention_line=float(load_config().raw["marks"]["attention_line"]))


# ---- scores and percentiles ----------------------------------------------------------------------


def score(marks: float, max_marks: float) -> float:
    """Marks as a share of the maximum, 0 to 1."""
    return marks / max_marks


def percentiles(scores: Sequence[float]) -> list[float]:
    """Mid-rank percentile (0 to 100) of each score within the list, in the order given."""
    n = len(scores)
    ordered = sorted(scores)
    out: list[float] = []
    for value in scores:
        below = sum(1 for other in ordered if other < value)
        equal = sum(1 for other in ordered if other == value)
        out.append(100.0 * (below + equal / 2) / n)
    return out


# ---- which lectures a test covers --------------------------------------------------------------


@dataclass(frozen=True)
class LectureRef:
    """The few facts about a lecture that decide whether a test covers it. `date` is local."""

    id: int
    teacher: int
    subject: int
    section: int | None
    date: date


@dataclass(frozen=True)
class OtherTest:
    """Another test of the same teacher and subject: its date and the sections it had students from."""

    date: date
    sections: frozenset[int]


def previous_dates(test_date: date, others: Iterable[OtherTest], sections: Iterable[int]) -> dict[int, date | None]:
    """For each section, the date of the latest earlier test that included it (None for the first)."""
    out: dict[int, date | None] = {}
    others = list(others)
    for section in sections:
        earlier = [o.date for o in others if o.date < test_date and section in o.sections]
        out[section] = max(earlier) if earlier else None
    return out


def covered_lectures(
    lectures: Iterable[LectureRef],
    *,
    teacher: int,
    subject: int,
    test_date: date,
    previous: Mapping[int, date | None],
) -> list[int]:
    """Ids of the lectures a test covers, in the order given. `previous` maps each section of the
    test to its previous test's date (None when there is none); sections not in it are not covered."""
    out: list[int] = []
    for x in lectures:
        if x.teacher != teacher or x.subject != subject or x.section is None or x.section not in previous:
            continue
        since = previous[x.section]
        if (since is None or x.date > since) and x.date <= test_date:
            out.append(x.id)
    return out


# ---- the four groups -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MarkResult:
    """One student's score (marks / max_marks) in a test."""

    student: str
    score: float


@dataclass(frozen=True)
class GroupedStudent:
    student: str
    score: float
    marks_percentile: float
    attention: float | None  # None when the student has insufficient data
    reason: str | None  # why there is no attention value
    attention_percentile: float | None
    group: str | None  # None when the student has insufficient data


@dataclass(frozen=True)
class Grouping:
    median: float | None  # median score of everybody in the test; None for an empty test
    students: list[GroupedStudent]  # in the order the results were given


def group_name(attentive: bool, marks_above: bool) -> str:
    if attentive:
        return ON_TRACK if marks_above else ATTENTIVE_BUT_STRUGGLING
    return KNOWS_IT if marks_above else AT_RISK


def assign_groups(
    results: Sequence[MarkResult],
    seconds: Mapping[str, Seconds],
    min_presence: float,
    attention_line: float,
) -> Grouping:
    """Place every student of a test in one of the four groups, or in none for insufficient data.

    `seconds` is each student's summed seconds over the covered lectures (a student missing from it
    has no records).
    """
    if not results:
        return Grouping(None, [])
    class_median = float(median(r.score for r in results))
    marks_percentiles = percentiles([r.score for r in results])
    figures = {
        r.student: metrics.attention_figure(seconds.get(r.student, Seconds()), min_presence) for r in results
    }
    with_value = [r.student for r in results if figures[r.student].value is not None]
    attention_percentiles = dict(
        zip(with_value, percentiles([float(figures[s].value or 0.0) for s in with_value]), strict=True)
    )
    students: list[GroupedStudent] = []
    for r, marks_percentile in zip(results, marks_percentiles, strict=True):
        figure = figures[r.student]
        group = (
            None
            if figure.value is None
            else group_name(figure.value > attention_line, r.score > class_median)
        )
        students.append(
            GroupedStudent(
                student=r.student,
                score=r.score,
                marks_percentile=marks_percentile,
                attention=figure.value,
                reason=figure.reason,
                attention_percentile=attention_percentiles.get(r.student),
                group=group,
            )
        )
    return Grouping(class_median, students)


# ---- the marks figure ----------------------------------------------------------------------------


@dataclass(frozen=True)
class MarkEntry:
    """One student's percentile in one test of one teacher."""

    student: str
    test: int
    teacher: int
    percentile: float


@dataclass(frozen=True)
class MarksFigure:
    """The marks figure of one teacher in percentile points, with its range and the tests it is from."""

    value: float | None
    low: float | None
    high: float | None
    n_students: int
    n_tests: int


def marks_figures(entries: Iterable[MarkEntry], range_level: float) -> dict[int, MarksFigure]:
    """The figure of every teacher that has a test. A teacher without one is absent."""
    rows = list(entries)
    own: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    everything: dict[str, list[float]] = defaultdict(list)
    tests: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        own[r.teacher][r.student].append(r.percentile)
        everything[r.student].append(r.percentile)
        tests[r.teacher].add(r.test)
    out: dict[int, MarksFigure] = {}
    for teacher, per_student in own.items():
        differences = [mean(values) - mean(everything[student]) for student, values in per_student.items()]
        figure = teacher_figures.figure_with_range(differences, range_level)
        out[teacher] = MarksFigure(figure.value, figure.low, figure.high, figure.n_students, len(tests[teacher]))
    return out
