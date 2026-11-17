"""Request and response shapes of the REST API."""

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.lectures import MANUAL
from backend.metrics import AttentionFigure
from backend.models import Lecture, Student, TimetableSlot
from backend.teacher_figures import Figure as TeacherFigure
from backend.timetable import DAYS, format_time


class IngestResult(BaseModel):
    received: int  # records in the request
    inserted: int  # new rows
    updated: int  # existing (student, window_start) rows that were replaced


class LectureOut(BaseModel):
    """A lecture, with the names the dashboard shows. Times are UTC."""

    id: int
    source: str  # "timetable" or "manual"
    room_id: str
    teacher_id: int
    teacher: str
    subject_id: int
    subject: str
    section_id: int | None
    section: str | None
    start: datetime
    end: datetime | None  # None while a manual session is open
    open: bool  # an open manual session
    simulated: bool  # made from simulated data

    @classmethod
    def from_row(cls, row: Lecture) -> "LectureOut":
        return cls(
            id=row.id,
            source=row.source,
            room_id=row.room_id,
            teacher_id=row.teacher_id,
            teacher=row.teacher.name,
            subject_id=row.subject_id,
            subject=row.subject.name,
            section_id=row.section_id,
            section=row.section.name if row.section else None,
            start=row.starts_at.astimezone(UTC),
            end=row.ends_at.astimezone(UTC) if row.ends_at else None,
            open=row.source == MANUAL and row.ends_at is None,
            simulated=row.simulated,
        )


class LiveWindow(BaseModel):
    window_start: datetime
    attentive: float
    neutral: float
    distracted: float
    not_visible: float
    attention: float | None
    presence: float | None
    lecture: LectureOut | None  # None for an unassigned window


class LiveResponse(BaseModel):
    register_no: str
    simulated: bool  # the student is simulated
    minutes: int
    windows: list[LiveWindow]  # oldest first


class NamedItem(BaseModel):
    id: int
    name: str
    simulated: bool = False


class RoomOut(BaseModel):
    id: str
    cameras: list[str]
    simulated: bool = False


class CameraRegistration(BaseModel):
    room_id: str = Field(min_length=1)
    simulated: bool = False  # flags a camera (and a room) created by this call


class CameraOut(BaseModel):
    id: str
    room_id: str
    camera_created: bool
    room_created: bool
    simulated: bool = False


class StudentCreate(BaseModel):
    register_no: str = Field(min_length=1, max_length=40)
    name: str | None = Field(default=None, max_length=120)
    section: str | None = Field(default=None, min_length=1, max_length=60)  # created if new
    simulated: bool = False
    archetype: str | None = Field(default=None, max_length=40)


class StudentOut(BaseModel):
    register_no: str
    name: str | None
    section: str | None
    simulated: bool
    archetype: str | None

    @classmethod
    def from_row(cls, row: Student) -> "StudentOut":
        return cls(
            register_no=row.register_no,
            name=row.name,
            section=row.section.name if row.section else None,
            simulated=row.simulated,
            archetype=row.archetype,
        )


class SlotOut(BaseModel):
    id: int
    day: str  # Mon..Sun
    day_index: int  # 0 = Monday
    start: str  # HH:MM local time
    end: str
    room: str
    section: str
    subject: str
    teacher: str
    simulated: bool = False

    @classmethod
    def from_row(cls, row: TimetableSlot) -> "SlotOut":
        return cls(
            id=row.id,
            day=DAYS[row.day_of_week],
            day_index=row.day_of_week,
            start=format_time(row.start_time),
            end=format_time(row.end_time),
            room=row.room_id,
            section=row.section.name,
            subject=row.subject.name,
            teacher=row.teacher.name,
            simulated=row.simulated,
        )


class TimetableOut(BaseModel):
    timezone: str  # slot times are wall-clock times in this zone
    slots: list[SlotOut]


class RowErrorOut(BaseModel):
    row: int
    message: str


class TimetableUploadResult(BaseModel):
    applied: bool  # False when any row is invalid: nothing was changed
    slots_total: int  # slots in the template after the upload
    slots_added: int
    slots_kept: int
    slots_removed: int
    created: dict[str, int]  # new teachers, subjects, rooms, sections
    errors: list[RowErrorOut]


class SessionStart(BaseModel):
    room_id: str = Field(min_length=1)
    teacher_id: int
    subject_id: int
    section_id: int | None = None


# ---- student profile ------------------------------------------------------------------


class FigureOut(BaseModel):
    """Attention over some period, with the seconds it was summed from.

    `value` is None when it cannot be shown; `reason` then says why:
    "no_records", "low_presence" (insufficient data: in view less than
    `min_presence_ratio` of the time) or "no_scored_time".
    """

    value: float | None
    reason: str | None
    presence: float | None
    attentive: float
    neutral: float
    distracted: float
    not_visible: float

    @classmethod
    def from_figure(cls, figure: AttentionFigure) -> "FigureOut":
        return cls(
            value=figure.value,
            reason=figure.reason,
            presence=figure.presence,
            attentive=figure.seconds.attentive,
            neutral=figure.seconds.neutral,
            distracted=figure.seconds.distracted,
            not_visible=figure.seconds.not_visible,
        )


class NamedFigure(BaseModel):
    id: int
    name: str
    figure: FigureOut


class HourFigure(BaseModel):
    hour: int  # local hour of the day, 0 to 23
    label: str  # "10:00"
    figure: FigureOut


class WeekFigure(BaseModel):
    week_start: date  # the Monday of the week, local date
    figure: FigureOut  # value is None for a week that fails the visibility gate


class AttendanceRow(BaseModel):
    lecture: LectureOut
    presence: float | None  # in view / the lecture's scheduled length, at most 1
    present: bool
    in_progress: bool  # still running, so the figure may still change
    figure: FigureOut  # attention in this lecture


class MonthAttendance(BaseModel):
    label: str  # "March 2026"
    present: int
    total: int  # lectures this month the student has records in
    min_presence_ratio: float


class ProfileResponse(BaseModel):
    register_no: str
    simulated: bool  # the student is simulated
    timezone: str  # the zone used for "local" hours, weeks and months
    now: datetime
    min_presence_ratio: float
    overall: FigureOut
    by_subject: list[NamedFigure]
    by_teacher: list[NamedFigure]
    by_time_of_day: list[HourFigure]  # only hours with records, ascending
    weekly: list[WeekFigure]  # first week with records to this week, oldest first
    attendance: list[AttendanceRow]  # newest lecture first
    month: MonthAttendance


class LecturePresence(BaseModel):
    ratio: float | None  # in view / elapsed time of the lecture, at most 1
    visible_seconds: float
    elapsed_seconds: float


class LectureDistracted(BaseModel):
    seconds: float
    share_of_visible: float | None  # None when the student was never in view


class CurrentResponse(BaseModel):
    register_no: str
    simulated: bool  # the student is simulated
    timezone: str
    now: datetime
    min_presence_ratio: float
    lecture: LectureOut | None  # the lecture running now that the student has records in
    attention_last_5_min: FigureOut
    attention_average: FigureOut  # the student's overall figure
    presence_this_lecture: LecturePresence | None
    distracted_this_lecture: LectureDistracted | None
    month: MonthAttendance


# ---- simulation ---------------------------------------------------------------------------


class EffectIn(BaseModel):
    """One built-in effect in attention points: kind "teacher" (name), "slot" ("14:00") or "section" (name)."""

    kind: Literal["teacher", "slot", "section"]
    key: str = Field(min_length=1, max_length=120)
    value: float


class SimulationIn(BaseModel):
    effects: list[EffectIn]
    weeks: int | None = Field(default=None, ge=1, le=520)
    seed: int | None = None


class SimulationSummary(BaseModel):
    """What the loaded simulation holds. `GET /api/simulation` returns null when nothing is loaded."""

    students: int
    teachers: int
    sections: list[str]
    rooms: int
    lectures: int
    records: int
    weeks: int | None
    seed: int | None
    effects: list[EffectIn]
    archetypes: dict[str, int]  # simulated students per archetype


class WipeResult(BaseModel):
    records: int
    lectures: int
    slots: int
    students: int
    teachers: int
    rooms: int
    cameras: int
    effects: int


# ---- teacher feedback ------------------------------------------------------------------------


class TeacherFigureOut(BaseModel):
    """A teacher figure in percentage points, with its range over `n_students` students.

    All three numbers are None while the figures are hidden or when no student has enough data.
    """

    value: float | None
    low: float | None
    high: float | None
    n_students: int

    @classmethod
    def from_figure(cls, figure: TeacherFigure) -> "TeacherFigureOut":
        return cls(value=figure.value, low=figure.low, high=figure.high, n_students=figure.n_students)


class TeacherLectureRow(BaseModel):
    lecture: LectureOut
    present: int  # students in view for at least `min_presence_ratio` of the lecture's length
    expected: int  # the section's size, or the students with records when the lecture has no section
    in_progress: bool
    attention: FigureOut  # the whole lecture, from every student's summed seconds


class TeacherAttendance(BaseModel):
    present: int
    expected: int
    rate: float | None  # present / expected over the teacher's lectures; None with no lectures


class TeacherSummary(BaseModel):
    teacher_id: int
    teacher: str
    simulated: bool
    n_lectures: int  # lectures with records
    hidden: bool  # fewer lectures than `min_lectures`
    reason: str | None  # why there is no value: "fewer than 5 lectures", or no student had enough data
    class_average: float | None  # plain attention (0 to 1) over all seconds in the teacher's lectures; kept while hidden
    self_comparison: TeacherFigureOut  # the teacher's students against their own average, points
    slot_adjusted: TeacherFigureOut  # the same after removing each time slot's average effect
    attendance: TeacherAttendance


class SectionAttendance(BaseModel):
    """Attendance across one section's lectures. No individual student appears here."""

    section_id: int
    section: str
    simulated: bool
    n_students: int  # students who belong to the section
    n_lectures: int  # the section's lectures with records
    present: int  # students in view for at least `min_presence_ratio` of a lecture, summed over lectures
    expected: int
    rate: float | None  # present / expected; None with no lectures


class AdminOverview(BaseModel):
    """Everything the admin view shows, from one pass over the records.

    Teachers and sections are alphabetical. Nothing here is about an individual student.
    """

    timezone: str
    now: datetime
    min_lectures: int
    range_level: float
    teachers: list[TeacherSummary]
    sections: list[SectionAttendance]
    unassigned_windows: int  # attention records (30-second windows) with no lecture


class TeacherFeedback(TeacherSummary):
    timezone: str
    now: datetime
    min_lectures: int
    range_level: float
    lectures: list[TeacherLectureRow]  # newest first


class TimelinePoint(BaseModel):
    window: int  # 0 for the first 30 seconds of the lecture
    offset_seconds: float  # window start, seconds after the lecture started
    students: int  # students with a record in the window
    attentive: float
    distracted: float
    attention: float | None  # attentive / (attentive + distracted); None with neither


class TimelineResponse(BaseModel):
    lecture: LectureOut
    window_seconds: int
    points: list[TimelinePoint]  # only windows that have records, oldest first
