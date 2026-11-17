"""Marks in the database: uploading a test, the teacher's tests, the four student groups, a student's
own marks and the marks figure of each teacher. The definitions are in `backend.marks`.

An upload is all or nothing: if any row is invalid, or any register number is not a known student,
nothing is stored and every problem is reported by line number. The percentiles of a test depend on
every row of it, so storing part of a file would give wrong percentiles. Uploading the same teacher,
subject, date and name again replaces the marks of that test.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend import marks as marks_logic
from backend.marks import GROUP_ORDER, LectureRef, MarkEntry, MarkResult, MarksFigure, MarksSettings, OtherTest
from backend.marks_file import RowError, parse_marks_file
from backend.metrics import Seconds
from backend.models import AttentionRecordRow, Lecture, Mark, MarksTest, Section, Student, Subject, Teacher
from backend.schemas import (
    GroupedStudentOut,
    GroupsResponse,
    MarksTestOut,
    MarksUploadResult,
    RowErrorOut,
    StudentMarkRow,
    StudentMarksResponse,
)

INSUFFICIENT = "Insufficient data"
MAX_NAME_LENGTH = 80


@dataclass(frozen=True)
class UploadRequest:
    teacher: Teacher
    subject: Subject
    test_date: date
    name: str
    simulated: bool


def clean_name(name: str | None) -> str:
    return " ".join((name or "").split())


def upload_marks(session: Session, raw: bytes, filename: str, request: UploadRequest) -> MarksUploadResult:
    """Validate the file and, only when every row is good, store (or replace) the test.

    Raises `MarksFileError` when the file cannot be read at all. A test is simulated when the
    request says so or its teacher is.
    """
    parsed = parse_marks_file(raw, filename)
    known = {r.casefold(): r for r in session.scalars(select(Student.register_no))}
    errors = list(parsed.errors)
    good = []
    for row in parsed.rows:
        register_no = known.get(row.register_no.casefold())
        if register_no is None:
            errors.append(RowError(row.row, f"register number {row.register_no} is not a known student"))
        else:
            good.append((register_no, row))
    if errors:
        return MarksUploadResult(
            applied=False,
            test_id=None,
            rows=0,
            replaced=False,
            errors=[RowErrorOut(row=e.row, message=e.message) for e in sorted(errors, key=lambda e: e.row)],
        )

    simulated = request.simulated or request.teacher.simulated
    test = session.scalar(
        select(MarksTest).where(
            MarksTest.teacher_id == request.teacher.id,
            MarksTest.subject_id == request.subject.id,
            MarksTest.test_date == request.test_date,
            MarksTest.name == request.name,
            MarksTest.simulated == simulated,
        )
    )
    replaced = test is not None
    if test is None:
        test = MarksTest(
            teacher_id=request.teacher.id,
            subject_id=request.subject.id,
            test_date=request.test_date,
            name=request.name,
            simulated=simulated,
        )
        session.add(test)
        session.flush()
    else:
        session.execute(delete(Mark).where(Mark.test_id == test.id))
    percentiles = marks_logic.percentiles([marks_logic.score(row.marks, row.max_marks) for _, row in good])
    session.add_all(
        Mark(test_id=test.id, student_id=register_no, marks=row.marks, max_marks=row.max_marks, percentile=percentile)
        for (register_no, row), percentile in zip(good, percentiles, strict=True)
    )
    session.commit()
    return MarksUploadResult(applied=True, test_id=test.id, rows=len(good), replaced=replaced, errors=[])


# ---- tests ----------------------------------------------------------------------------------------


def describe_tests(session: Session, tests: list[MarksTest]) -> list[MarksTestOut]:
    ids = [t.id for t in tests]
    counts: dict[int, int] = {}
    sections: dict[int, set[str]] = defaultdict(set)
    if ids:
        counts = {
            test_id: int(n)
            for test_id, n in session.execute(
                select(Mark.test_id, func.count()).where(Mark.test_id.in_(ids)).group_by(Mark.test_id)
            )
        }
        for test_id, name in session.execute(
            select(Mark.test_id, Section.name)
            .join(Student, Student.register_no == Mark.student_id)
            .join(Section, Section.id == Student.section_id)
            .where(Mark.test_id.in_(ids))
            .distinct()
        ):
            sections[test_id].add(name)
    return [
        MarksTestOut(
            id=t.id,
            name=t.name,
            teacher_id=t.teacher_id,
            teacher=t.teacher.name,
            subject_id=t.subject_id,
            subject=t.subject.name,
            test_date=t.test_date,
            simulated=t.simulated,
            n_students=counts.get(t.id, 0),
            sections=sorted(sections.get(t.id, set())),
        )
        for t in tests
    ]


def teacher_tests(session: Session, teacher_id: int) -> list[MarksTestOut]:
    """The teacher's tests, newest first."""
    tests = list(
        session.scalars(
            select(MarksTest)
            .where(MarksTest.teacher_id == teacher_id)
            .order_by(MarksTest.test_date.desc(), MarksTest.id.desc())
        ).unique()
    )
    return describe_tests(session, tests)


# ---- groups -------------------------------------------------------------------------------------


def _covered_lecture_ids(session: Session, test: MarksTest, sections: set[int]) -> list[int]:
    others: dict[date, set[int]] = defaultdict(set)
    for other_date, section_id in session.execute(
        select(MarksTest.test_date, Student.section_id)
        .join(Mark, Mark.test_id == MarksTest.id)
        .join(Student, Student.register_no == Mark.student_id)
        .where(
            MarksTest.teacher_id == test.teacher_id,
            MarksTest.subject_id == test.subject_id,
            MarksTest.id != test.id,
            Student.section_id.is_not(None),
        )
        .distinct()
    ):
        if section_id is not None:
            others[other_date].add(section_id)
    previous = marks_logic.previous_dates(
        test.test_date, [OtherTest(d, frozenset(s)) for d, s in others.items()], sections
    )
    lectures = [
        LectureRef(lecture_id, teacher_id, subject_id, section_id, lecture_date)
        for lecture_id, teacher_id, subject_id, section_id, lecture_date in session.execute(
            select(Lecture.id, Lecture.teacher_id, Lecture.subject_id, Lecture.section_id, Lecture.lecture_date)
            .where(
                Lecture.teacher_id == test.teacher_id,
                Lecture.subject_id == test.subject_id,
                Lecture.section_id.in_(sections),
                Lecture.lecture_date <= test.test_date,
            )
            .order_by(Lecture.starts_at, Lecture.id)
        )
    ]
    return marks_logic.covered_lectures(
        lectures, teacher=test.teacher_id, subject=test.subject_id, test_date=test.test_date, previous=previous
    )


def _summed_seconds(session: Session, lecture_ids: list[int], students: list[str]) -> dict[str, Seconds]:
    totals = [
        func.coalesce(func.sum(c), 0.0)
        for c in (
            AttentionRecordRow.attentive,
            AttentionRecordRow.neutral,
            AttentionRecordRow.distracted,
            AttentionRecordRow.not_visible,
        )
    ]
    grouped = session.execute(
        select(AttentionRecordRow.student_id, *totals)
        .where(AttentionRecordRow.lecture_id.in_(lecture_ids), AttentionRecordRow.student_id.in_(students))
        .group_by(AttentionRecordRow.student_id)
    )
    return {student_id: Seconds(*(float(v) for v in values)) for student_id, *values in grouped}


def build_groups(
    session: Session, test: MarksTest, min_presence: float, settings: MarksSettings, include_students: bool
) -> GroupsResponse:
    """The four groups of a test. Students are listed only when `include_students` (the teacher's view)."""
    rows = session.execute(
        select(Mark, Student)
        .join(Student, Student.register_no == Mark.student_id)
        .where(Mark.test_id == test.id)
        .order_by(Mark.student_id)
    ).all()
    sections = {s.section_id for _, s in rows if s.section_id is not None}
    covered = _covered_lecture_ids(session, test, sections) if sections else []
    seconds = _summed_seconds(session, covered, [m.student_id for m, _ in rows]) if covered and rows else {}
    grouping = marks_logic.assign_groups(
        [MarkResult(m.student_id, marks_logic.score(m.marks, m.max_marks)) for m, _ in rows],
        seconds,
        min_presence,
        settings.attention_line,
    )
    counts = {name: 0 for name in GROUP_ORDER}
    counts[INSUFFICIENT] = 0
    for g in grouping.students:
        counts[g.group or INSUFFICIENT] += 1
    students = None
    if include_students:
        by_register = {m.student_id: (m, s) for m, s in rows}
        students = []
        for g in grouping.students:
            mark, student = by_register[g.student]
            students.append(
                GroupedStudentOut(
                    register_no=student.register_no,
                    name=student.name,
                    section=student.section.name if student.section else None,
                    marks=mark.marks,
                    max_marks=mark.max_marks,
                    marks_percentile=g.marks_percentile,
                    attention=g.attention,
                    attention_percentile=g.attention_percentile,
                    reason=g.reason,
                    group=g.group,
                )
            )
    return GroupsResponse(
        test=describe_tests(session, [test])[0],
        attention_line=settings.attention_line,
        median_score=grouping.median,
        n_students=len(rows),
        n_covered_lectures=len(covered),
        counts=counts,
        students=students,
    )


# ---- a student's own marks -----------------------------------------------------------------------------


def student_marks(session: Session, student: Student) -> StudentMarksResponse:
    """The student's marks and percentile in each test, newest first. Only this student's rows."""
    rows = (
        session.execute(
            select(Mark, MarksTest)
            .join(MarksTest, MarksTest.id == Mark.test_id)
            .where(Mark.student_id == student.register_no)
            .order_by(MarksTest.test_date.desc(), MarksTest.id.desc())
        )
        .unique()
        .all()
    )
    sizes: dict[int, int] = {}
    if rows:
        sizes = {
            test_id: int(n)
            for test_id, n in session.execute(
                select(Mark.test_id, func.count())
                .where(Mark.test_id.in_([t.id for _, t in rows]))
                .group_by(Mark.test_id)
            )
        }
    return StudentMarksResponse(
        register_no=student.register_no,
        simulated=student.simulated,
        tests=[
            StudentMarkRow(
                test_id=t.id,
                name=t.name,
                subject=t.subject.name,
                teacher=t.teacher.name,
                test_date=t.test_date,
                marks=m.marks,
                max_marks=m.max_marks,
                percentile=m.percentile,
                n_students=sizes.get(t.id, 0),
            )
            for m, t in rows
        ],
    )


# ---- the teachers' marks figure -------------------------------------------------------------------------


def marks_figures(session: Session, range_level: float) -> dict[int, MarksFigure]:
    """Every teacher's marks figure from one query over all marks. A teacher with no tests is absent."""
    entries = [
        MarkEntry(student, test, teacher, percentile)
        for student, test, teacher, percentile in session.execute(
            select(Mark.student_id, Mark.test_id, MarksTest.teacher_id, Mark.percentile).join(
                MarksTest, MarksTest.id == Mark.test_id
            )
        )
    ]
    return marks_logic.marks_figures(entries, range_level)
