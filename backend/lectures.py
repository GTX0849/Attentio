"""Lecture resolution and manual sessions.

A record belongs to the lecture found from its camera's room and the instant
in the middle of its window (window_start plus half the window length):

1. a manual session in that room that covers the instant wins;
2. otherwise the timetable slot of that room covering the local weekday and time;
3. otherwise the record has no lecture (unassigned).

A lecture is one occurrence of a slot on a date, or one manual session. It is
created by the first record that needs it and reused after that.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.models import Lecture, TimetableSlot

TIMETABLE = "timetable"
MANUAL = "manual"


def resolve_lecture(
    session: Session,
    room_id: str,
    window_start: datetime,
    window_seconds: int,
    tz: ZoneInfo,
) -> int | None:
    """The id of the lecture for a window in a room, creating it if needed."""
    instant = window_start.astimezone(UTC) + timedelta(seconds=window_seconds / 2)

    session_id = session.scalar(
        select(Lecture.id)
        .where(
            Lecture.room_id == room_id,
            Lecture.source == MANUAL,
            Lecture.starts_at <= instant,
            (Lecture.ends_at.is_(None)) | (Lecture.ends_at > instant),
        )
        .order_by(Lecture.starts_at.desc())
        .limit(1)
    )
    if session_id is not None:
        return session_id

    local = instant.astimezone(tz)
    local_time = local.time()
    slot = session.scalar(
        select(TimetableSlot).where(
            TimetableSlot.room_id == room_id,
            TimetableSlot.day_of_week == local.weekday(),
            TimetableSlot.start_time <= local_time,
            TimetableSlot.end_time > local_time,
        )
    )
    if slot is None:
        return None

    lecture_date = local.date()
    starts_at = datetime.combine(lecture_date, slot.start_time, tzinfo=tz).astimezone(UTC)
    ends_at = datetime.combine(lecture_date, slot.end_time, tzinfo=tz).astimezone(UTC)
    # Insert-or-ignore so two requests racing for the same occurrence share one lecture.
    session.execute(
        insert(Lecture)
        .values(
            source=TIMETABLE,
            room_id=slot.room_id,
            teacher_id=slot.teacher_id,
            subject_id=slot.subject_id,
            section_id=slot.section_id,
            slot_id=slot.id,
            lecture_date=lecture_date,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        .on_conflict_do_nothing(index_elements=["slot_id", "lecture_date"])
    )
    return session.scalar(
        select(Lecture.id).where(Lecture.slot_id == slot.id, Lecture.lecture_date == lecture_date)
    )
