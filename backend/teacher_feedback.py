"""What the teacher view shows, read from the database.

One pass over the records, grouped by student and lecture, feeds `backend.teacher_figures`; the
same pass gives every lecture's attendance and attention. Both the one-teacher answer and the
all-teachers answer are built from it, so asking for everyone costs no more queries than asking
for one. The slot-adjusted figure needs every teacher's data, so even the one-teacher answer reads
all lectures, but it returns nothing about any other teacher.

Records with no lecture are never read. A lecture's "expected" count is its section's size, or the
number of students with records when it has no section. The in-lecture timeline groups records
into 30-second windows counted from the lecture's start.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from backend import metrics, teacher_figures
from backend.lectures import MANUAL
from backend.metrics import Seconds
from backend.models import AttentionRecordRow, Lecture, Section, Student, Teacher
from backend.schemas import (
    AdminOverview,
    FigureOut,
    LectureOut,
    SectionAttendance,
    TeacherAttendance,
    TeacherFeedback,
    TeacherFigureOut,
    TeacherLectureRow,
    TeacherSummary,
    TimelinePoint,
    TimelineResponse,
)
from backend.teacher_figures import StudentLecture, TeacherFigures, TeacherSettings

Row = AttentionRecordRow


def slot_of(lecture: Lecture, tz: ZoneInfo) -> str:
    """The local start time, "14:00"; a manual session uses its start hour ("14:00" for 14:37)."""
    local = lecture.starts_at.astimezone(tz)
    return f"{local.hour:02d}:00" if lecture.source == MANUAL else f"{local:%H:%M}"


@dataclass
class _Data:
    lectures: dict[int, Lecture]
    cells: dict[int, dict[str, Seconds]]  # lecture id -> student -> summed seconds
    section_sizes: dict[int, int]


def _load(session: Session) -> _Data:
    """Every student's summed seconds per lecture (one grouped query), and the lectures."""
    totals = [func.coalesce(func.sum(c), 0.0) for c in (Row.attentive, Row.neutral, Row.distracted, Row.not_visible)]
    grouped = session.execute(
        select(Row.student_id, Row.lecture_id, *totals)
        .where(Row.lecture_id.is_not(None))
        .group_by(Row.student_id, Row.lecture_id)
    )
    cells: dict[int, dict[str, Seconds]] = defaultdict(dict)
    for student, lecture_id, *values in grouped:
        if lecture_id is None:
            continue
        cells[lecture_id][student] = Seconds(*(float(v) for v in values))
    lectures = {x.id: x for x in session.scalars(select(Lecture).where(Lecture.id.in_(list(cells)))).unique()} if cells else {}
    sizes = {
        section_id: int(count)
        for section_id, count in session.execute(
            select(Student.section_id, func.count()).where(Student.section_id.is_not(None)).group_by(Student.section_id)
        )
        if section_id is not None
    }
    return _Data(lectures, cells, sizes)


def _figures(data: _Data, tz: ZoneInfo, settings: TeacherSettings, min_presence: float) -> dict[int, TeacherFigures]:
    rows = [
        StudentLecture(student, lecture_id, data.lectures[lecture_id].teacher_id, slot_of(data.lectures[lecture_id], tz), seconds)
        for lecture_id, students in data.cells.items()
        for student, seconds in students.items()
    ]
    return teacher_figures.teacher_figures(rows, settings, min_presence)


def _lecture_row(data: _Data, lecture: Lecture, now: datetime, min_presence: float) -> TeacherLectureRow:
    students = data.cells.get(lecture.id, {})
    scheduled = metrics.scheduled_seconds(lecture.starts_at, lecture.ends_at, now)
    present = sum(
        1 for s in students.values() if metrics.is_present(metrics.lecture_presence(s, scheduled), min_presence)
    )
    size = data.section_sizes.get(lecture.section_id, 0) if lecture.section_id is not None else 0
    return TeacherLectureRow(
        lecture=LectureOut.from_row(lecture),
        present=present,
        expected=max(size, len(students)),
        in_progress=lecture.ends_at is None or lecture.ends_at > now,
        attention=FigureOut.from_figure(
            metrics.attention_figure(metrics.total_seconds(students.values()), min_presence)
        ),
    )


def _class_averages(data: _Data, min_presence: float) -> dict[int, float | None]:
    """Plain attention per teacher: summed seconds of every record in their lectures, with the gate."""
    sums: dict[int, Seconds] = defaultdict(Seconds)
    for lecture_id, students in data.cells.items():
        teacher_id = data.lectures[lecture_id].teacher_id
        sums[teacher_id] = sums[teacher_id] + metrics.total_seconds(students.values())
    return {t: metrics.attention_figure(total, min_presence).value for t, total in sums.items()}


def _summary(
    teacher: Teacher,
    figures: TeacherFigures | None,
    rows: list[TeacherLectureRow],
    settings: TeacherSettings,
    class_average: float | None,
) -> TeacherSummary:
    present = sum(r.present for r in rows)
    expected = sum(r.expected for r in rows)
    empty = teacher_figures.Figure(None, None, None, 0)
    if figures is None:
        figures = TeacherFigures(
            teacher.id, 0, True, teacher_figures.hidden_reason(settings.min_lectures), empty, empty
        )
    return TeacherSummary(
        teacher_id=teacher.id,
        teacher=teacher.name,
        simulated=teacher.simulated,
        n_lectures=figures.n_lectures,
        hidden=figures.hidden,
        reason=figures.reason,
        class_average=class_average,
        self_comparison=TeacherFigureOut.from_figure(figures.self_comparison),
        slot_adjusted=TeacherFigureOut.from_figure(figures.slot_adjusted),
        attendance=TeacherAttendance(
            present=present, expected=expected, rate=present / expected if expected else None
        ),
    )


def _lecture_rows(data: _Data, now: datetime, min_presence: float) -> list[TeacherLectureRow]:
    """One row per lecture with records, newest first."""
    ordered = sorted(data.lectures.values(), key=lambda x: (x.starts_at, x.id), reverse=True)
    return [_lecture_row(data, lecture, now, min_presence) for lecture in ordered]


def _teacher_rows(rows: list[TeacherLectureRow]) -> dict[int, list[TeacherLectureRow]]:
    out: dict[int, list[TeacherLectureRow]] = defaultdict(list)
    for row in rows:
        out[row.lecture.teacher_id].append(row)
    return out


def _section_attendance(session: Session, data: _Data, rows: list[TeacherLectureRow]) -> list[SectionAttendance]:
    """Present over expected across each section's lectures, and its size. Every section is listed."""
    by_section: dict[int, list[TeacherLectureRow]] = defaultdict(list)
    for row in rows:
        if row.lecture.section_id is not None:
            by_section[row.lecture.section_id].append(row)
    out = []
    for section in session.scalars(select(Section).order_by(Section.name)):
        lectures = by_section.get(section.id, [])
        present = sum(r.present for r in lectures)
        expected = sum(r.expected for r in lectures)
        out.append(
            SectionAttendance(
                section_id=section.id,
                section=section.name,
                simulated=section.simulated,
                n_students=data.section_sizes.get(section.id, 0),
                n_lectures=len(lectures),
                present=present,
                expected=expected,
                rate=present / expected if expected else None,
            )
        )
    return out


def _unassigned_windows(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Row).where(Row.lecture_id.is_(None))) or 0)


def _everyone(
    session: Session, data: _Data, now: datetime, tz: ZoneInfo, settings: TeacherSettings, min_presence: float
) -> tuple[list[TeacherSummary], list[TeacherLectureRow]]:
    figures = _figures(data, tz, settings, min_presence)
    rows = _lecture_rows(data, now, min_presence)
    by_teacher = _teacher_rows(rows)
    averages = _class_averages(data, min_presence)
    teachers = session.scalars(select(Teacher).order_by(Teacher.name)).all()
    summaries = [
        _summary(t, figures.get(t.id), by_teacher.get(t.id, []), settings, averages.get(t.id)) for t in teachers
    ]
    return summaries, rows


def build_all(
    session: Session, now: datetime, tz: ZoneInfo, settings: TeacherSettings, min_presence: float
) -> list[TeacherSummary]:
    """Every teacher's figures and attendance, by name."""
    return _everyone(session, _load(session), now, tz, settings, min_presence)[0]


def build_overview(
    session: Session, now: datetime, tz: ZoneInfo, settings: TeacherSettings, min_presence: float
) -> AdminOverview:
    """Teachers, sections and the unassigned count for the admin view, from one pass over the records.

    Only totals and figures per teacher and per section: no individual student is returned.
    """
    data = _load(session)
    summaries, rows = _everyone(session, data, now, tz, settings, min_presence)
    return AdminOverview(
        timezone=tz.key,
        now=now,
        min_lectures=settings.min_lectures,
        range_level=settings.range_level,
        teachers=summaries,
        sections=_section_attendance(session, data, rows),
        unassigned_windows=_unassigned_windows(session),
    )


def build_one(
    session: Session,
    teacher: Teacher,
    now: datetime,
    tz: ZoneInfo,
    settings: TeacherSettings,
    min_presence: float,
) -> TeacherFeedback:
    """One teacher's feedback. Only that teacher's lectures are returned."""
    data = _load(session)
    figures = _figures(data, tz, settings, min_presence)
    rows = _teacher_rows(_lecture_rows(data, now, min_presence)).get(teacher.id, [])
    average = _class_averages(data, min_presence).get(teacher.id)
    summary = _summary(teacher, figures.get(teacher.id), rows, settings, average)
    return TeacherFeedback(
        **summary.model_dump(),
        timezone=tz.key,
        now=now,
        min_lectures=settings.min_lectures,
        range_level=settings.range_level,
        lectures=rows,
    )


def build_timeline(session: Session, lecture: Lecture, window_seconds: int) -> TimelineResponse:
    """Class attention per window: attentive / (attentive + distracted) over every student's seconds."""
    offset = func.extract("epoch", Row.window_start - lecture.starts_at)
    window = func.greatest(0, func.floor(offset / window_seconds)).cast(Integer)
    grouped = session.execute(
        select(
            window.label("window"),
            func.coalesce(func.sum(Row.attentive), 0.0),
            func.coalesce(func.sum(Row.distracted), 0.0),
            func.count(func.distinct(Row.student_id)),
        )
        .where(Row.lecture_id == lecture.id)
        .group_by(window)
        .order_by(window)
    )
    return TimelineResponse(
        lecture=LectureOut.from_row(lecture),
        window_seconds=window_seconds,
        points=[
            TimelinePoint(
                window=index,
                offset_seconds=index * window_seconds,
                students=int(students),
                attentive=float(attentive),
                distracted=float(distracted),
                attention=metrics.attention(float(attentive), float(distracted)),
            )
            for index, attentive, distracted, students in grouped
        ],
    )
