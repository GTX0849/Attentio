"""Request and response shapes of the REST API."""

from datetime import UTC, date, datetime

from pydantic import BaseModel, Field

from backend.lectures import MANUAL
from backend.metrics import AttentionFigure
from backend.models import Lecture, TimetableSlot
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
    minutes: int
    windows: list[LiveWindow]  # oldest first


class NamedItem(BaseModel):
    id: int
    name: str


class RoomOut(BaseModel):
    id: str
    cameras: list[str]


class CameraRegistration(BaseModel):
    room_id: str = Field(min_length=1)


class CameraOut(BaseModel):
    id: str
    room_id: str
    camera_created: bool
    room_created: bool


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
    timezone: str
    now: datetime
    min_presence_ratio: float
    lecture: LectureOut | None  # the lecture running now that the student has records in
    attention_last_5_min: FigureOut
    attention_average: FigureOut  # the student's overall figure
    presence_this_lecture: LecturePresence | None
    distracted_this_lecture: LectureDistracted | None
    month: MonthAttendance
