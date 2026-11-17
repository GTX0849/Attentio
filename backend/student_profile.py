"""Everything the student view shows, worked out from one student's own records.

Only rows with `student_id == register_no` are ever read, so nothing about
other students can reach the answer. Sums are done in SQL (grouped by lecture,
local hour or local week) and the figures come from `backend.metrics` applied
to those summed seconds.

Which records count where:

- overall, live, time-of-day and weekly figures use every record, including
  unassigned ones (no lecture);
- by-subject, by-teacher, attendance and the current-lecture stats use only
  records tied to a lecture.

"Time of day" is the local hour of a window's start (10:00 covers 10:00 to
10:59), in the backend's time zone. A week starts on Monday, local time.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Date, Integer, func, or_, select
from sqlalchemy.orm import Session

from backend import metrics
from backend.metrics import Seconds
from backend.models import AttentionRecordRow, Lecture
from backend.schemas import (
    AttendanceRow,
    CurrentResponse,
    FigureOut,
    HourFigure,
    LectureDistracted,
    LectureOut,
    LecturePresence,
    MonthAttendance,
    NamedFigure,
    ProfileResponse,
    WeekFigure,
)

LIVE_MINUTES = 5

Row = AttentionRecordRow


def _sums(
    session: Session,
    register_no: str,
    group_by: Sequence[Any] = (),
    where: Sequence[ColumnElement[bool]] = (),
) -> dict[tuple[Any, ...], Seconds]:
    """This student's summed seconds, grouped by the given expressions (one total if none)."""
    totals = [
        func.coalesce(func.sum(column), 0.0)
        for column in (Row.attentive, Row.neutral, Row.distracted, Row.not_visible)
    ]
    stmt = select(*group_by, *totals).where(Row.student_id == register_no, *where)
    if group_by:
        stmt = stmt.group_by(*group_by)
    width = len(group_by)
    return {
        tuple(row[:width]): Seconds(*(float(v) for v in row[width:]))
        for row in session.execute(stmt)
    }


def _total(
    session: Session, register_no: str, where: Sequence[ColumnElement[bool]] = ()
) -> Seconds:
    return _sums(session, register_no, where=where).get((), Seconds())


def _lectures(session: Session, ids: Sequence[int]) -> dict[int, Lecture]:
    if not ids:
        return {}
    rows = session.scalars(select(Lecture).where(Lecture.id.in_(ids))).unique()
    return {lecture.id: lecture for lecture in rows}


def _attendance(
    session: Session, register_no: str, now: datetime, min_presence: float
) -> list[AttendanceRow]:
    """One row per lecture the student has records in, newest first."""
    sums = _sums(session, register_no, [Row.lecture_id], [Row.lecture_id.is_not(None)])
    lectures = _lectures(session, [key[0] for key in sums])
    rows: list[AttendanceRow] = []
    for lecture in sorted(lectures.values(), key=lambda x: (x.starts_at, x.id), reverse=True):
        seconds = sums[(lecture.id,)]
        scheduled = metrics.scheduled_seconds(lecture.starts_at, lecture.ends_at, now)
        ratio = metrics.lecture_presence(seconds, scheduled)
        rows.append(
            AttendanceRow(
                lecture=LectureOut.from_row(lecture),
                presence=ratio,
                present=metrics.is_present(ratio, min_presence),
                in_progress=lecture.ends_at is None or lecture.ends_at > now,
                figure=FigureOut.from_figure(metrics.attention_figure(seconds, min_presence)),
            )
        )
    return rows


def _month(
    rows: list[AttendanceRow], now: datetime, tz: ZoneInfo, min_presence: float
) -> MonthAttendance:
    local_now = now.astimezone(tz)
    this_month = [
        r
        for r in rows
        if (start := r.lecture.start.astimezone(tz)).year == local_now.year
        and start.month == local_now.month
    ]
    return MonthAttendance(
        label=f"{local_now:%B %Y}",
        present=sum(1 for r in this_month if r.present),
        total=len(this_month),
        min_presence_ratio=min_presence,
    )


def _breakdown(
    attendance_sums: dict[int, Seconds],
    lectures: dict[int, Lecture],
    key: str,
    min_presence: float,
) -> list[NamedFigure]:
    """Sum the lectures that share a subject (or teacher), then apply the gate once."""
    groups: dict[int, tuple[str, list[Seconds]]] = {}
    for lecture_id, seconds in attendance_sums.items():
        lecture = lectures[lecture_id]
        item = getattr(lecture, key)
        groups.setdefault(item.id, (item.name, []))[1].append(seconds)
    return [
        NamedFigure(
            id=item_id,
            name=name,
            figure=FigureOut.from_figure(
                metrics.attention_figure(metrics.total_seconds(parts), min_presence)
            ),
        )
        for item_id, (name, parts) in sorted(groups.items(), key=lambda kv: kv[1][0])
    ]


def build_profile(
    session: Session, register_no: str, now: datetime, tz: ZoneInfo, min_presence: float
) -> ProfileResponse:
    overall = metrics.attention_figure(_total(session, register_no), min_presence)

    by_lecture = {
        key[0]: seconds
        for key, seconds in _sums(
            session, register_no, [Row.lecture_id], [Row.lecture_id.is_not(None)]
        ).items()
    }
    lectures = _lectures(session, list(by_lecture))

    local_start = func.timezone(tz.key, Row.window_start)
    hour = func.extract("hour", local_start).cast(Integer)
    by_hour = _sums(session, register_no, [hour])
    week = func.date_trunc("week", local_start).cast(Date)
    by_week = {key[0]: seconds for key, seconds in _sums(session, register_no, [week]).items()}

    attendance = _attendance(session, register_no, now, min_presence)
    return ProfileResponse(
        register_no=register_no,
        timezone=tz.key,
        now=now,
        min_presence_ratio=min_presence,
        overall=FigureOut.from_figure(overall),
        by_subject=_breakdown(by_lecture, lectures, "subject", min_presence),
        by_teacher=_breakdown(by_lecture, lectures, "teacher", min_presence),
        by_time_of_day=[
            HourFigure(
                hour=int(key[0]),
                label=f"{int(key[0]):02d}:00",
                figure=FigureOut.from_figure(metrics.attention_figure(seconds, min_presence)),
            )
            for key, seconds in sorted(by_hour.items())
        ],
        weekly=_weekly(by_week, now, tz, min_presence),
        attendance=attendance,
        month=_month(attendance, now, tz, min_presence),
    )


def _weekly(
    by_week: dict[date, Seconds], now: datetime, tz: ZoneInfo, min_presence: float
) -> list[WeekFigure]:
    """Every week from the first with records to the current one; empty weeks are blank."""
    if not by_week:
        return []
    today = now.astimezone(tz).date()
    this_week = today - timedelta(days=today.weekday())
    week = min(by_week)
    last = max(this_week, max(by_week))
    out: list[WeekFigure] = []
    while week <= last:
        figure = metrics.attention_figure(by_week.get(week, Seconds()), min_presence)
        out.append(WeekFigure(week_start=week, figure=FigureOut.from_figure(figure)))
        week += timedelta(days=7)
    return out


def build_current(
    session: Session, register_no: str, now: datetime, tz: ZoneInfo, min_presence: float
) -> CurrentResponse:
    last_5 = _total(
        session,
        register_no,
        [Row.window_start >= now - timedelta(minutes=LIVE_MINUTES), Row.window_start <= now],
    )
    overall = _total(session, register_no)

    # The running lecture the student has records in (the newest one if several overlap).
    lecture = session.scalars(
        select(Lecture)
        .where(
            Lecture.starts_at <= now,
            or_(Lecture.ends_at.is_(None), Lecture.ends_at > now),
            Lecture.id.in_(
                select(Row.lecture_id).where(
                    Row.student_id == register_no, Row.lecture_id.is_not(None)
                )
            ),
        )
        .order_by(Lecture.starts_at.desc(), Lecture.id.desc())
        .limit(1)
    ).first()

    presence: LecturePresence | None = None
    distracted: LectureDistracted | None = None
    if lecture is not None:
        seconds = _total(session, register_no, [Row.lecture_id == lecture.id])
        elapsed = metrics.elapsed_seconds(lecture.starts_at, lecture.ends_at, now)
        presence = LecturePresence(
            ratio=metrics.lecture_presence(seconds, elapsed),
            visible_seconds=min(seconds.visible, elapsed),
            elapsed_seconds=elapsed,
        )
        distracted = LectureDistracted(
            seconds=seconds.distracted,
            share_of_visible=seconds.distracted / seconds.visible if seconds.visible > 0 else None,
        )

    attendance = _attendance(session, register_no, now, min_presence)
    return CurrentResponse(
        register_no=register_no,
        timezone=tz.key,
        now=now,
        min_presence_ratio=min_presence,
        lecture=LectureOut.from_row(lecture) if lecture is not None else None,
        attention_last_5_min=FigureOut.from_figure(metrics.attention_figure(last_5, min_presence)),
        attention_average=FigureOut.from_figure(metrics.attention_figure(overall, min_presence)),
        presence_this_lecture=presence,
        distracted_this_lecture=distracted,
        month=_month(attendance, now, tz, min_presence),
    )

