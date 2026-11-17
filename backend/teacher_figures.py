"""Teacher figures: how a teacher's students do in their lectures, compared with themselves.

Plain functions over summed seconds (see `backend.metrics`). Nothing here reads the database; the
backend feeds in one `StudentLecture` per student and lecture and reads the figures out.

Records with no lecture (`lecture is None`) are ignored throughout. Attention is attentive /
(attentive + distracted) from SUMMED seconds, with the visibility gate: a student's time in a
lecture counts only when they were in view for at least `scores.min_presence_ratio` of the
seconds recorded for it; below that it is "insufficient data" and the cell is dropped everywhere
(it adds nothing to the student's own average either).

Definitions (all in percentage points, "points"):

1. For each student s and lecture L: d(s, L) = attention(s, L) - own(s), where own(s) is the
   attention of s over the summed seconds of ALL their lectures (every teacher, including the
   one being measured).
2. Self-comparison for teacher T: for each student, the mean of d over T's lectures they
   attended; the figure is the mean of those student means. Each student counts once, however
   many of T's lectures they attended, and each lecture counts once, however long the student
   was in view. It is NOT weighted by seconds; the seconds enter only inside attention(s, L)
   and own(s).
3. Slot effect for a slot k (the local start time of the lecture, such as "14:00"; a manual
   session uses its start hour, "14:00" for any start in 14:00 to 14:59): the mean of d over all
   (s, L) in slot k, across ALL teachers, unweighted.
4. Slot-adjusted for T: the same as the self-comparison but with d'(s, L) = d(s, L) -
   slot_effect(slot of L).
5. Range: across T's students, mean +/- z * sd / sqrt(n), with sd the sample standard deviation
   (n - 1) of the per-student means and z the normal quantile for `teachers.range_level` (1.96
   for 0.95). This is a normal approximation, fine for the cohort sizes here and rough for very
   few students. With one student there is no range (low and high are None).
6. Hiding: until T has `teachers.min_lectures` lectures with records (any record, whether or not
   it passes the gate) every figure is hidden: value and range are None and the reason is
   "fewer than 5 lectures".

What this can and cannot do. Comparing a student with their own average removes differences
between students and between sections: a teacher of a strong section is not flattered. It does
not remove the effect of the time slot, so a teacher who mostly teaches at a bad time of day is
still marked down; that is what the slot-adjusted figure is for. The slot-adjusted figure cannot
separate a teacher from a slot they ALWAYS teach in: if only T ever teaches at 14:00, the 14:00
effect and T's effect are the same number and slot-adjusting removes both.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import NormalDist, mean, stdev

from backend import metrics
from backend.metrics import Seconds
from shared.config import load_config

NO_STUDENTS = "no students with enough time in view"


@dataclass(frozen=True)
class TeacherSettings:
    min_lectures: int  # figures stay hidden below this many lectures with records
    range_level: float  # e.g. 0.95


def teacher_settings() -> TeacherSettings:
    """The `teachers:` section of the shared config."""
    section = load_config().raw["teachers"]
    return TeacherSettings(
        min_lectures=int(section["min_lectures"]), range_level=float(section["range_level"])
    )


def hidden_reason(min_lectures: int) -> str:
    return f"fewer than {min_lectures} lectures"


@dataclass(frozen=True)
class StudentLecture:
    """One student's summed seconds in one lecture. `lecture` is None for unassigned records.

    `teacher` and `slot` (the lecture's local start time, "14:00") are the lecture's own.
    """

    student: str
    lecture: int | None
    teacher: int | None
    slot: str | None
    seconds: Seconds


@dataclass(frozen=True)
class Delta:
    """A student's attention in one lecture minus their own average, in points."""

    student: str
    lecture: int
    teacher: int
    slot: str
    points: float


@dataclass(frozen=True)
class Figure:
    """A figure in points with its range, over `n_students` students. None when it cannot be shown."""

    value: float | None
    low: float | None
    high: float | None
    n_students: int


@dataclass(frozen=True)
class TeacherFigures:
    teacher: int
    n_lectures: int  # lectures with records
    hidden: bool  # fewer lectures than the minimum
    reason: str | None  # why the figures have no value, if they do not
    self_comparison: Figure
    slot_adjusted: Figure


def deltas(rows: Iterable[StudentLecture], min_presence: float) -> list[Delta]:
    """d(s, L) for every student and lecture that passes the visibility gate."""
    cells: dict[tuple[str, int], tuple[int, str, Seconds]] = {}
    for row in rows:
        if row.lecture is None or row.teacher is None or row.slot is None:
            continue
        key = (row.student, row.lecture)
        previous = cells.get(key)
        total = row.seconds if previous is None else previous[2] + row.seconds
        cells[key] = (row.teacher, row.slot, total)

    shown: dict[tuple[str, int], float] = {}
    sums: dict[str, Seconds] = defaultdict(Seconds)
    for (student, lecture), (_, _, seconds) in cells.items():
        value = metrics.attention_figure(seconds, min_presence).value
        if value is not None:
            shown[(student, lecture)] = value
            sums[student] = sums[student] + seconds

    own: dict[str, float] = {}
    for student, seconds in sums.items():
        average = metrics.attention(seconds.attentive, seconds.distracted)
        if average is not None:
            own[student] = average

    return [
        Delta(student, lecture, cells[(student, lecture)][0], cells[(student, lecture)][1], 100 * (value - own[student]))
        for (student, lecture), value in shown.items()
        if student in own
    ]


def slot_effects(all_deltas: Iterable[Delta]) -> dict[str, float]:
    """The mean difference per slot, across all teachers."""
    by_slot: dict[str, list[float]] = defaultdict(list)
    for d in all_deltas:
        by_slot[d.slot].append(d.points)
    return {slot: mean(points) for slot, points in by_slot.items()}


def z_value(level: float) -> float:
    """Two-sided normal quantile: 1.96 for a 0.95 range."""
    return NormalDist().inv_cdf((1 + level) / 2)


def figure_with_range(student_means: list[float], level: float) -> Figure:
    """Mean of the per-student values with its range; one student gives no range."""
    n = len(student_means)
    if n == 0:
        return Figure(None, None, None, 0)
    centre = mean(student_means)
    if n == 1:
        return Figure(centre, None, None, 1)
    half = z_value(level) * stdev(student_means) / n**0.5
    return Figure(centre, centre - half, centre + half, n)


def _student_means(values: dict[str, list[float]]) -> list[float]:
    return [mean(v) for v in values.values()]


def teacher_figures(
    rows: Iterable[StudentLecture], settings: TeacherSettings, min_presence: float
) -> dict[int, TeacherFigures]:
    """The figures of every teacher that has a lecture with records, from one pass over the rows."""
    materialised = [r for r in rows if r.lecture is not None and r.teacher is not None]
    lectures_of: dict[int, set[int]] = defaultdict(set)
    for r in materialised:
        assert r.teacher is not None and r.lecture is not None
        lectures_of[r.teacher].add(r.lecture)

    all_deltas = deltas(materialised, min_presence)
    effects = slot_effects(all_deltas)
    plain: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    adjusted: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for d in all_deltas:
        plain[d.teacher][d.student].append(d.points)
        adjusted[d.teacher][d.student].append(d.points - effects[d.slot])

    out: dict[int, TeacherFigures] = {}
    for teacher, lecture_ids in lectures_of.items():
        n_lectures = len(lecture_ids)
        if n_lectures < settings.min_lectures:
            empty = Figure(None, None, None, 0)
            out[teacher] = TeacherFigures(
                teacher, n_lectures, True, hidden_reason(settings.min_lectures), empty, empty
            )
            continue
        own = figure_with_range(_student_means(plain[teacher]), settings.range_level)
        slotted = figure_with_range(_student_means(adjusted[teacher]), settings.range_level)
        reason = NO_STUDENTS if own.value is None else None
        out[teacher] = TeacherFigures(teacher, n_lectures, False, reason, own, slotted)
    return out
