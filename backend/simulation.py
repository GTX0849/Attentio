"""The loaded simulation: its stored true effects, a summary, and the wipe.

Simulated data is flagged `simulated` on students, rooms, cameras, teachers,
subjects, sections, timetable slots, lectures and marks tests. Records are simulated when
their student is; marks go with their test or their student. The wipe deletes exactly those rows and the stored effects;
live rows are never touched. A simulated teacher, subject, section, room or
camera that something live still points to is kept.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, Executable, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session

from backend.models import (
    AttentionRecordRow,
    Camera,
    Lecture,
    Mark,
    MarksTest,
    Room,
    Section,
    SimulationEffect,
    Student,
    Subject,
    Teacher,
    TimetableSlot,
)
from backend.schemas import EffectIn, SimulationIn, SimulationSummary, WipeResult

META = "meta"
EFFECT_KINDS = ("teacher", "slot", "section")


def store_effects(session: Session, body: SimulationIn) -> None:
    """Replace the stored effects (and run details) with the given ones."""
    session.execute(delete(SimulationEffect))
    session.add_all(SimulationEffect(kind=e.kind, key=e.key, value=e.value) for e in body.effects)
    if body.weeks is not None:
        session.add(SimulationEffect(kind=META, key="weeks", value=body.weeks))
    if body.seed is not None:
        session.add(SimulationEffect(kind=META, key="seed", value=body.seed))
    session.commit()


def summary(session: Session) -> SimulationSummary | None:
    """What is loaded, or None when there is no simulation (no simulated students and no effects)."""
    stored = list(session.scalars(select(SimulationEffect).order_by(SimulationEffect.id)))
    students = session.scalar(
        select(func.count()).select_from(Student).where(Student.simulated)
    ) or 0
    if not stored and students == 0:
        return None
    meta = {e.key: e.value for e in stored if e.kind == META}
    archetypes = {
        str(name): count
        for name, count in session.execute(
            select(Student.archetype, func.count())
            .where(Student.simulated, Student.archetype.is_not(None))
            .group_by(Student.archetype)
            .order_by(Student.archetype)
        )
    }
    return SimulationSummary(
        students=students,
        teachers=session.scalar(select(func.count()).select_from(Teacher).where(Teacher.simulated))
        or 0,
        sections=list(session.scalars(select(Section.name).where(Section.simulated).order_by(Section.name))),
        rooms=session.scalar(select(func.count()).select_from(Room).where(Room.simulated)) or 0,
        lectures=session.scalar(select(func.count()).select_from(Lecture).where(Lecture.simulated))
        or 0,
        records=session.scalar(
            select(func.count())
            .select_from(AttentionRecordRow)
            .join(Student, Student.register_no == AttentionRecordRow.student_id)
            .where(Student.simulated)
        )
        or 0,
        weeks=int(meta["weeks"]) if "weeks" in meta else None,
        seed=int(meta["seed"]) if "seed" in meta else None,
        effects=[
            EffectIn.model_validate({"kind": e.kind, "key": e.key, "value": e.value})
            for e in stored
            if e.kind in EFFECT_KINDS
        ],
        archetypes=archetypes,
    )


def _run(session: Session, statement: Executable) -> int:
    """Execute a delete or update and return how many rows it touched."""
    return cast(CursorResult[Any], session.execute(statement)).rowcount


def wipe(session: Session) -> WipeResult:
    """Delete all simulated data, in foreign key order, and report how much of each."""
    simulated_students = select(Student.register_no).where(Student.simulated)
    # Marks of simulated tests and of simulated students; a live test that is left with no marks goes too.
    marks = _run(
        session,
        delete(Mark).where(
            or_(
                Mark.student_id.in_(simulated_students),
                Mark.test_id.in_(select(MarksTest.id).where(MarksTest.simulated)),
            )
        ),
    )
    tests = _run(
        session,
        delete(MarksTest).where(
            or_(MarksTest.simulated, ~exists().where(Mark.test_id == MarksTest.id))
        ),
    )
    records = _run(
        session,
        delete(AttentionRecordRow).where(AttentionRecordRow.student_id.in_(simulated_students))
    )
    # A live record that somehow points at a simulated lecture keeps existing, unassigned.
    _run(
        session,
        update(AttentionRecordRow)
        .where(AttentionRecordRow.lecture_id.in_(select(Lecture.id).where(Lecture.simulated)))
        .values(lecture_id=None)
    )
    lectures = _run(session, delete(Lecture).where(Lecture.simulated))
    slots = _run(session, delete(TimetableSlot).where(TimetableSlot.simulated))
    students = _run(session, delete(Student).where(Student.simulated))

    cameras = _run(
        session,
        delete(Camera).where(
            Camera.simulated, ~exists().where(AttentionRecordRow.camera_id == Camera.id)
        )
    )
    rooms = _run(
        session,
        delete(Room).where(
            Room.simulated,
            ~exists().where(Camera.room_id == Room.id),
            ~exists().where(TimetableSlot.room_id == Room.id),
            ~exists().where(Lecture.room_id == Room.id),
        )
    )
    teachers = _run(
        session,
        delete(Teacher).where(
            Teacher.simulated,
            ~exists().where(TimetableSlot.teacher_id == Teacher.id),
            ~exists().where(Lecture.teacher_id == Teacher.id),
            ~exists().where(MarksTest.teacher_id == Teacher.id),
        )
    )
    _run(
        session,
        delete(Subject).where(
            Subject.simulated,
            ~exists().where(TimetableSlot.subject_id == Subject.id),
            ~exists().where(Lecture.subject_id == Subject.id),
            ~exists().where(MarksTest.subject_id == Subject.id),
        )
    )
    _run(
        session,
        delete(Section).where(
            Section.simulated,
            ~exists().where(TimetableSlot.section_id == Section.id),
            ~exists().where(Lecture.section_id == Section.id),
            ~exists().where(Student.section_id == Section.id),
        )
    )
    effects = _run(session, delete(SimulationEffect))
    session.commit()
    return WipeResult(
        records=records,
        lectures=lectures,
        slots=slots,
        students=students,
        teachers=teachers,
        rooms=rooms,
        cameras=cameras,
        effects=effects,
        tests=tests,
        marks=marks,
    )
