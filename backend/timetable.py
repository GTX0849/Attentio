"""Weekly timetable CSV: parsing, validation and replacing the template.

A new upload replaces its own template: a normal upload replaces only the
non-simulated slots and a simulated upload only the simulated ones. Slots that
are identical to ones already stored are kept (same id), so re-uploading the
same file changes nothing and lectures already created from those slots keep their link. Lectures
copy room, teacher, subject and section, so they survive slots being removed.

The upload is all or nothing: if any row is invalid, nothing is changed and
every bad row is reported.
"""

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import time
from typing import Generic, Protocol, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models import Room, Section, Subject, Teacher, TimetableSlot

COLUMNS = ["day", "start", "end", "room", "section", "subject", "teacher"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_LOOKUP = {name.lower(): i for i, name in enumerate(DAYS)}
_DAY_LOOKUP.update(
    {
        full: i
        for i, full in enumerate(
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        )
    }
)
_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")

# day, start, end, room id, section id, subject id, teacher id
SlotKey = tuple[int, time, time, str, int, int, int]


class TimetableFileError(Exception):
    """The file as a whole cannot be read (missing columns, wrong encoding, empty)."""


@dataclass(frozen=True)
class RowError:
    row: int  # line number in the file; the header is line 1
    message: str


@dataclass
class ParsedRow:
    row: int
    day: int
    start: time
    end: time
    room: str
    section: str
    subject: str
    teacher: str


@dataclass
class ParsedTimetable:
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)


@dataclass
class ApplyResult:
    applied: bool
    slots_total: int
    slots_added: int
    slots_kept: int
    slots_removed: int
    created: dict[str, int]
    errors: list[RowError]


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_time(text: str) -> time | None:
    m = _TIME_PATTERN.match(text)
    if m is None:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def format_time(value: time) -> str:
    return f"{value:%H:%M}"


def parse_csv(raw: bytes) -> ParsedTimetable:
    """Parse and validate the file. Row problems are collected, not raised."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise TimetableFileError("The file is not UTF-8 text. Save it as CSV (UTF-8)") from e
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise TimetableFileError("The file is empty")
    reader.fieldnames = [_clean(name).lower() for name in reader.fieldnames]
    missing = [c for c in COLUMNS if c not in reader.fieldnames]
    if missing:
        raise TimetableFileError(
            f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(COLUMNS)}"
        )

    parsed = ParsedTimetable()
    for line_no, record in enumerate(reader, start=2):
        values = {c: _clean(record.get(c)) for c in COLUMNS}
        if not any(values.values()):
            continue  # blank line
        problems: list[str] = []
        day = _DAY_LOOKUP.get(values["day"].lower())
        if day is None:
            problems.append(f"day '{values['day']}' is not one of {', '.join(DAYS)}")
        start = _parse_time(values["start"])
        if start is None:
            problems.append(f"start '{values['start']}' is not a time like 09:30")
        end = _parse_time(values["end"])
        if end is None:
            problems.append(f"end '{values['end']}' is not a time like 10:20")
        if start is not None and end is not None and end <= start:
            problems.append("end must be after start")
        for name in ("room", "section", "subject", "teacher"):
            if not values[name]:
                problems.append(f"{name} is empty")
        if problems or day is None or start is None or end is None:
            parsed.errors.append(RowError(line_no, "; ".join(problems)))
            continue
        row = ParsedRow(
            line_no,
            day,
            start,
            end,
            values["room"],
            values["section"],
            values["subject"],
            values["teacher"],
        )
        clash = next(
            (
                other
                for other in parsed.rows
                if other.day == row.day
                and other.room.casefold() == row.room.casefold()
                and row.start < other.end
                and other.start < row.end
            ),
            None,
        )
        if clash is not None:
            parsed.errors.append(
                RowError(
                    line_no,
                    f"overlaps row {clash.row} in room {row.room} on {DAYS[row.day]} "
                    f"({format_time(clash.start)} to {format_time(clash.end)})",
                )
            )
            continue
        parsed.rows.append(row)
    if not parsed.rows and not parsed.errors:
        raise TimetableFileError("The file has no rows")
    return parsed


class _Named(Protocol):
    @property
    def id(self) -> int | str: ...


E = TypeVar("E", bound=_Named)


class _Registry(Generic[E]):
    """Existing rows by case-insensitive name; creates the ones that are missing."""

    def __init__(
        self,
        session: Session,
        existing: list[E],
        name_of: Callable[[E], str],
        make: Callable[[str], E],
    ) -> None:
        self._session = session
        self._by_name = {name_of(e).casefold(): e for e in existing}
        self._make = make
        self.created = 0

    def get(self, name: str) -> E:
        entity = self._by_name.get(name.casefold())
        if entity is None:
            entity = self._make(name)
            self._session.add(entity)
            self._session.flush()
            self._by_name[name.casefold()] = entity
            self.created += 1
        return entity


def replace_timetable(session: Session, raw: bytes, simulated: bool = False) -> ApplyResult:
    """Validate the file and, only when every row is valid, make it the template.

    Only slots with the same `simulated` flag are replaced; entities created by
    the upload carry the flag too.
    """
    parsed = parse_csv(raw)
    if parsed.errors:
        return ApplyResult(
            applied=False,
            slots_total=0,
            slots_added=0,
            slots_kept=0,
            slots_removed=0,
            created={"teachers": 0, "subjects": 0, "rooms": 0, "sections": 0},
            errors=parsed.errors,
        )

    teachers = _Registry(session, list(session.scalars(select(Teacher))), lambda t: t.name, lambda n: Teacher(name=n, simulated=simulated))
    subjects = _Registry(session, list(session.scalars(select(Subject))), lambda s: s.name, lambda n: Subject(name=n, simulated=simulated))
    sections = _Registry(session, list(session.scalars(select(Section))), lambda s: s.name, lambda n: Section(name=n, simulated=simulated))
    rooms = _Registry(session, list(session.scalars(select(Room))), lambda r: r.id, lambda n: Room(id=n, simulated=simulated))

    wanted: set[SlotKey] = set()
    for row in parsed.rows:
        wanted.add(
            (
                row.day,
                row.start,
                row.end,
                rooms.get(row.room).id,
                sections.get(row.section).id,
                subjects.get(row.subject).id,
                teachers.get(row.teacher).id,
            )
        )

    existing = {
        (s.day_of_week, s.start_time, s.end_time, s.room_id, s.section_id, s.subject_id, s.teacher_id): s
        for s in session.scalars(select(TimetableSlot).where(TimetableSlot.simulated == simulated))
    }
    removed_ids = [slot.id for key, slot in existing.items() if key not in wanted]
    if removed_ids:
        session.execute(delete(TimetableSlot).where(TimetableSlot.id.in_(removed_ids)))
    added = [key for key in wanted if key not in existing]
    session.add_all(
        TimetableSlot(
            day_of_week=d,
            start_time=s,
            end_time=e,
            room_id=r,
            section_id=sec,
            subject_id=sub,
            teacher_id=t,
            simulated=simulated,
        )
        for (d, s, e, r, sec, sub, t) in added
    )
    session.commit()
    return ApplyResult(
        applied=True,
        slots_total=len(wanted),
        slots_added=len(added),
        slots_kept=len(wanted) - len(added),
        slots_removed=len(removed_ids),
        created={
            "teachers": teachers.created,
            "subjects": subjects.created,
            "rooms": rooms.created,
            "sections": sections.created,
        },
        errors=[],
    )
