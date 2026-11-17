"""Database tables."""

from datetime import date, datetime, time

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _simulated_flag() -> Mapped[bool]:
    """A NOT NULL boolean, false for live data. server_default lets create_all-less upgrades add it."""
    return mapped_column(default=False, server_default=text("false"))


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(primary_key=True)  # e.g. "R101"
    simulated: Mapped[bool] = _simulated_flag()
    cameras: Mapped[list["Camera"]] = relationship(back_populates="room")


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(primary_key=True)  # e.g. "CAM1"
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    simulated: Mapped[bool] = _simulated_flag()
    room: Mapped[Room] = relationship(back_populates="cameras")


class Student(Base):
    __tablename__ = "students"

    register_no: Mapped[str] = mapped_column(primary_key=True)  # e.g. "STU001"
    name: Mapped[str | None] = mapped_column(default=None)
    # A student with a section is expected at every lecture of that section (attendance).
    section_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id"), default=None)
    simulated: Mapped[bool] = _simulated_flag()
    # Only simulated students have one: how the simulator generated them (read by the marks ticket).
    archetype: Mapped[str | None] = mapped_column(default=None)

    section: Mapped["Section | None"] = relationship(lazy="joined")


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    simulated: Mapped[bool] = _simulated_flag()


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    simulated: Mapped[bool] = _simulated_flag()


class Section(Base):
    """A class group, for example "CSE-A"."""

    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    simulated: Mapped[bool] = _simulated_flag()


class TimetableSlot(Base):
    """One weekly slot of the timetable template. Times are local wall-clock time."""

    __tablename__ = "timetable_slots"
    __table_args__ = (Index("ix_timetable_slots_room_day", "room_id", "day_of_week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day_of_week: Mapped[int]  # 0 = Monday ... 6 = Sunday
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    simulated: Mapped[bool] = _simulated_flag()

    section: Mapped[Section] = relationship(lazy="joined")
    subject: Mapped[Subject] = relationship(lazy="joined")
    teacher: Mapped[Teacher] = relationship(lazy="joined")


class Lecture(Base):
    """One occurrence of a timetable slot on a date, or one manual session.

    A lecture keeps its own copy of room, teacher, subject and section, so it
    stays valid when the timetable template is replaced. `slot_id` is only a
    link back to the slot that produced it and is cleared when that slot is
    removed.
    """

    __tablename__ = "lectures"
    __table_args__ = (
        # One lecture per slot per date. NULL slot_id (manual sessions) never collides.
        UniqueConstraint("slot_id", "lecture_date"),
        # Only one open manual session per room.
        Index(
            "uq_one_open_session_per_room",
            "room_id",
            unique=True,
            postgresql_where=text("source = 'manual' AND ends_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]  # "timetable" or "manual"
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    section_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id"), default=None)
    slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("timetable_slots.id", ondelete="SET NULL"), default=None
    )
    lecture_date: Mapped[date] = mapped_column(Date)  # local calendar date of the start
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    simulated: Mapped[bool] = _simulated_flag()

    teacher: Mapped[Teacher] = relationship(lazy="joined")
    subject: Mapped[Subject] = relationship(lazy="joined")
    section: Mapped[Section | None] = relationship(lazy="joined")


class AttentionRecordRow(Base):
    __tablename__ = "attention_records"
    __table_args__ = (UniqueConstraint("student_id", "window_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.register_no"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attentive: Mapped[float] = mapped_column(Float, default=0.0)
    neutral: Mapped[float] = mapped_column(Float, default=0.0)
    distracted: Mapped[float] = mapped_column(Float, default=0.0)
    not_visible: Mapped[float] = mapped_column(Float, default=0.0)
    # NULL for a record that matched no lecture (unassigned).
    lecture_id: Mapped[int | None] = mapped_column(
        ForeignKey("lectures.id"), default=None, index=True
    )
    lecture: Mapped[Lecture | None] = relationship()


class MarksTest(Base):
    """One test a teacher ran and uploaded marks for. It belongs to that teacher and subject.

    The same teacher, subject, date and name (with the same simulated flag) is one test:
    uploading it again replaces its marks. `name` is "" when the teacher gave none.
    """

    __tablename__ = "marks_tests"
    __table_args__ = (UniqueConstraint("teacher_id", "subject_id", "test_date", "name", "simulated"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    test_date: Mapped[date] = mapped_column(Date)  # local calendar date of the test
    name: Mapped[str] = mapped_column(default="", server_default=text("''"))
    simulated: Mapped[bool] = _simulated_flag()

    teacher: Mapped[Teacher] = relationship(lazy="joined")
    subject: Mapped[Subject] = relationship(lazy="joined")


class Mark(Base):
    """One student's marks in one test, with their percentile within that test (see backend/marks.py)."""

    __tablename__ = "marks"
    __table_args__ = (UniqueConstraint("test_id", "student_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("marks_tests.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.register_no"), index=True)
    marks: Mapped[float] = mapped_column(Float)
    max_marks: Mapped[float] = mapped_column(Float)
    percentile: Mapped[float] = mapped_column(Float)  # 0 to 100, mid-rank within the test


class SimulationEffect(Base):
    """A built-in effect of the loaded simulation, in attention points (percentage points).

    `kind` is "teacher" (key: teacher name), "slot" (key: start time such as "14:00"),
    "section" (key: section name), or "meta" (key: "weeks" or "seed", for the run itself).
    """

    __tablename__ = "simulation_effects"
    __table_args__ = (UniqueConstraint("kind", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    key: Mapped[str]
    value: Mapped[float] = mapped_column(Float)
