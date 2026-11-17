"""Lecture resolution and manual sessions.

A record belongs to the lecture found from its camera's room and the instant
in the middle of its window (window_start plus half the window length):

1. a manual session in that room that covers the instant wins;
2. otherwise the timetable slot of that room covering the local weekday and time;
3. otherwise the record has no lecture (unassigned).

A lecture is one occurrence of a slot on a date, or one manual session. It is
created by the first record that needs it and reused after that.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.models import Lecture, TimetableSlot

TIMETABLE = "timetable"
MANUAL = "manual"


class LectureResolver:
    """Finds (and creates) the lecture of a window, for many windows in one request.

    Each room's manual sessions and timetable slots are read once, and each lecture is
    looked up or created once, so a batch of thousands of records costs a handful of queries.
    The rules are the ones in the module docstring.
    """

    def __init__(self, session: Session, window_seconds: int, tz: ZoneInfo) -> None:
        self._session = session
        self._window_seconds = window_seconds
        self._tz = tz
        self._sessions: dict[str, list[Lecture]] = {}
        self._slots: dict[str, list[TimetableSlot]] = {}
        self._lectures: dict[tuple[int, date], int | None] = {}
        self._windows: dict[tuple[str, datetime], int | None] = {}

    def resolve(self, room_id: str, window_start: datetime) -> int | None:
        """The id of the lecture for a window in a room, creating it if needed."""
        key = (room_id, window_start.astimezone(UTC))
        if key not in self._windows:
            self._windows[key] = self._resolve(room_id, window_start)
        return self._windows[key]

    def _resolve(self, room_id: str, window_start: datetime) -> int | None:
        instant = window_start.astimezone(UTC) + timedelta(seconds=self._window_seconds / 2)

        if room_id not in self._sessions:
            self._sessions[room_id] = list(
                self._session.scalars(
                    select(Lecture).where(Lecture.room_id == room_id, Lecture.source == MANUAL)
                ).unique()
            )
        covering = [
            x
            for x in self._sessions[room_id]
            if x.starts_at <= instant and (x.ends_at is None or x.ends_at > instant)
        ]
        if covering:
            return max(covering, key=lambda x: x.starts_at).id

        local = instant.astimezone(self._tz)
        local_time = local.time()
        if room_id not in self._slots:
            self._slots[room_id] = list(
                self._session.scalars(
                    select(TimetableSlot).where(TimetableSlot.room_id == room_id)
                ).unique()
            )
        slot = next(
            (
                x
                for x in self._slots[room_id]
                if x.day_of_week == local.weekday() and x.start_time <= local_time < x.end_time
            ),
            None,
        )
        if slot is None:
            return None

        lecture_date = local.date()
        if (slot.id, lecture_date) not in self._lectures:
            self._lectures[(slot.id, lecture_date)] = self._timetable_lecture(slot, lecture_date)
        return self._lectures[(slot.id, lecture_date)]

    def _timetable_lecture(self, slot: TimetableSlot, lecture_date: date) -> int | None:
        starts_at = datetime.combine(lecture_date, slot.start_time, tzinfo=self._tz).astimezone(UTC)
        ends_at = datetime.combine(lecture_date, slot.end_time, tzinfo=self._tz).astimezone(UTC)
        # Insert-or-ignore so two requests racing for the same occurrence share one lecture.
        self._session.execute(
            insert(Lecture)
            .values(
                source=TIMETABLE,
                room_id=slot.room_id,
                teacher_id=slot.teacher_id,
                subject_id=slot.subject_id,
                section_id=slot.section_id,
                slot_id=slot.id,
                simulated=slot.simulated,
                lecture_date=lecture_date,
                starts_at=starts_at,
                ends_at=ends_at,
            )
            .on_conflict_do_nothing(index_elements=["slot_id", "lecture_date"])
        )
        return self._session.scalar(
            select(Lecture.id).where(
                Lecture.slot_id == slot.id, Lecture.lecture_date == lecture_date
            )
        )
