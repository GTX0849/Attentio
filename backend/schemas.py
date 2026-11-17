"""Request and response shapes of the REST API."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from backend.models import Lecture, TimetableSlot
from backend.lectures import MANUAL
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
