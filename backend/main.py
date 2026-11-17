"""FastAPI app: ingests attention records, serves recent windows, timetable and lectures."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import Engine, func, literal_column, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.dml import ReturningInsert

from backend import metrics
from backend.clock import Clock, resolve_timezone, system_clock
from backend.db import init_database
from backend.lectures import MANUAL, resolve_lecture
from backend.models import (
    AttentionRecordRow,
    Camera,
    Lecture,
    Room,
    Section,
    Student,
    Subject,
    Teacher,
    TimetableSlot,
)
from backend.schemas import (
    CameraOut,
    CameraRegistration,
    IngestResult,
    LectureOut,
    LiveResponse,
    LiveWindow,
    NamedItem,
    RoomOut,
    RowErrorOut,
    SessionStart,
    SlotOut,
    TimetableOut,
    TimetableUploadResult,
)
from backend.timetable import TimetableFileError, replace_timetable
from shared.config import database_url, load_config
from shared.schema import AttentionRecord as AttentionRecordIn

MAX_UPLOAD_BYTES = 1_000_000


def get_session(request: Request) -> Iterator[Session]:
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(
    url: str | None = None,
    *,
    timezone: str | None = None,
    clock: Clock = system_clock,
    window_seconds: int | None = None,
) -> FastAPI:
    """Build the app.

    `url` defaults to DATABASE_URL from .env. `timezone` is the IANA zone the
    timetable is written in (default: CLASSPULSE_TIMEZONE, else Asia/Kolkata).
    `clock` and `window_seconds` exist so tests can control time; the window
    length defaults to the one in config/classpulse.yaml.
    """
    tz: ZoneInfo = resolve_timezone(timezone)  # fail at start-up on a bad zone name
    window_len = window_seconds if window_seconds is not None else load_config().window_seconds

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = init_database(url or database_url())
        app.state.engine = engine
        yield
        engine.dispose()

    app = FastAPI(title="ClassPulse backend", lifespan=lifespan)

    # ---- records -------------------------------------------------------------

    @app.post("/api/records")
    def post_records(records: list[AttentionRecordIn], session: SessionDep) -> IngestResult:
        # The sender's window_start is trusted. Check every reference first so a
        # bad record rejects the whole batch instead of storing part of it.
        unknown_students = sorted(
            {r.student_id for r in records if session.get(Student, r.student_id) is None}
        )
        if unknown_students:
            raise HTTPException(404, f"Unknown student register number(s): {unknown_students}")
        cameras = {c.id: c for c in session.scalars(select(Camera))}
        unknown_cameras = sorted({r.camera_id for r in records if r.camera_id not in cameras})
        if unknown_cameras:
            raise HTTPException(404, f"Unknown camera id(s): {unknown_cameras}")

        # A repeated (student, window_start) inside one batch: the last one wins.
        latest = {(r.student_id, r.window_start): r for r in records}
        if not latest:
            return IngestResult(received=0, inserted=0, updated=0)

        # Records already stored keep their lecture; only the rest are resolved.
        keys = [(student, start.astimezone(UTC)) for student, start in latest]
        already_assigned = {
            (row[0], row[1])
            for row in session.execute(
                select(AttentionRecordRow.student_id, AttentionRecordRow.window_start).where(
                    tuple_(AttentionRecordRow.student_id, AttentionRecordRow.window_start).in_(keys),
                    AttentionRecordRow.lecture_id.is_not(None),
                )
            )
        }
        lecture_cache: dict[tuple[str, datetime], int | None] = {}

        def lecture_for(r: AttentionRecordIn) -> int | None:
            start = r.window_start.astimezone(UTC)
            if (r.student_id, start) in already_assigned:
                return None  # the stored lecture is kept by the upsert below
            cache_key = (cameras[r.camera_id].room_id, start)
            if cache_key not in lecture_cache:
                lecture_cache[cache_key] = resolve_lecture(
                    session, cache_key[0], start, window_len, tz
                )
            return lecture_cache[cache_key]

        stmt = insert(AttentionRecordRow).values(
            [
                {
                    "camera_id": r.camera_id,
                    "student_id": r.student_id,
                    "window_start": r.window_start.astimezone(UTC),
                    "attentive": r.attentive,
                    "neutral": r.neutral,
                    "distracted": r.distracted,
                    "not_visible": r.not_visible,
                    "lecture_id": lecture_for(r),
                }
                for r in latest.values()
            ]
        )
        upsert: ReturningInsert[tuple[bool]] = stmt.on_conflict_do_update(
            index_elements=["student_id", "window_start"],
            set_={
                "camera_id": stmt.excluded.camera_id,
                "attentive": stmt.excluded.attentive,
                "neutral": stmt.excluded.neutral,
                "distracted": stmt.excluded.distracted,
                "not_visible": stmt.excluded.not_visible,
                # A re-sent record keeps the lecture it already has; an unassigned one may gain one.
                "lecture_id": func.coalesce(
                    AttentionRecordRow.__table__.c.lecture_id, stmt.excluded.lecture_id
                ),
            },
        ).returning(literal_column("(xmax = 0)").label("inserted"))  # true: new row, false: replaced
        flags = [row.inserted for row in session.execute(upsert)]
        session.commit()
        inserted = sum(1 for f in flags if f)
        return IngestResult(received=len(records), inserted=inserted, updated=len(flags) - inserted)

    @app.get("/api/students/{register_no}/live")
    def get_live(
        register_no: str,
        session: SessionDep,
        minutes: Annotated[int, Query(ge=1, le=24 * 60)] = 10,
    ) -> LiveResponse:
        if session.get(Student, register_no) is None:
            raise HTTPException(404, f"Unknown student register number: {register_no}")
        since = clock() - timedelta(minutes=minutes)
        rows = session.scalars(
            select(AttentionRecordRow)
            .options(joinedload(AttentionRecordRow.lecture))
            .where(
                AttentionRecordRow.student_id == register_no,
                AttentionRecordRow.window_start >= since,
            )
            .order_by(AttentionRecordRow.window_start)
        ).all()
        return LiveResponse(
            register_no=register_no,
            minutes=minutes,
            windows=[
                LiveWindow(
                    window_start=r.window_start.astimezone(UTC),
                    attentive=r.attentive,
                    neutral=r.neutral,
                    distracted=r.distracted,
                    not_visible=r.not_visible,
                    attention=metrics.attention(r.attentive, r.distracted),
                    presence=metrics.presence(
                        r.attentive, r.neutral, r.distracted, r.not_visible
                    ),
                    lecture=LectureOut.from_row(r.lecture) if r.lecture else None,
                )
                for r in rows
            ],
        )

    # ---- timetable -----------------------------------------------------------

    @app.post("/api/timetable")
    def upload_timetable(
        session: SessionDep, file: Annotated[UploadFile, File()]
    ) -> TimetableUploadResult:
        raw = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file is larger than 1 MB")
        try:
            result = replace_timetable(session, raw)
        except TimetableFileError as e:
            raise HTTPException(422, str(e)) from e
        return TimetableUploadResult(
            applied=result.applied,
            slots_total=result.slots_total,
            slots_added=result.slots_added,
            slots_kept=result.slots_kept,
            slots_removed=result.slots_removed,
            created=result.created,
            errors=[RowErrorOut(row=e.row, message=e.message) for e in result.errors],
        )

    @app.get("/api/timetable")
    def get_timetable(session: SessionDep) -> TimetableOut:
        slots = session.scalars(
            select(TimetableSlot).order_by(
                TimetableSlot.day_of_week, TimetableSlot.start_time, TimetableSlot.room_id
            )
        ).all()
        return TimetableOut(timezone=tz.key, slots=[SlotOut.from_row(s) for s in slots])

    # ---- rooms, cameras and pickers -------------------------------------------

    @app.get("/api/rooms")
    def list_rooms(session: SessionDep) -> list[RoomOut]:
        rooms = session.scalars(select(Room).order_by(Room.id)).all()
        return [RoomOut(id=r.id, cameras=sorted(c.id for c in r.cameras)) for r in rooms]

    @app.put("/api/cameras/{camera_id}")
    def register_camera(
        camera_id: str, body: CameraRegistration, session: SessionDep
    ) -> CameraOut:
        """Put a camera in a room, creating the camera and the room when new."""
        room = session.get(Room, body.room_id)
        room_created = room is None
        if room is None:
            session.add(Room(id=body.room_id))
            session.flush()
        camera = session.get(Camera, camera_id)
        camera_created = camera is None
        if camera is None:
            session.add(Camera(id=camera_id, room_id=body.room_id))
        else:
            camera.room_id = body.room_id
        session.commit()
        return CameraOut(
            id=camera_id,
            room_id=body.room_id,
            camera_created=camera_created,
            room_created=room_created,
        )

    @app.get("/api/teachers")
    def list_teachers(session: SessionDep) -> list[NamedItem]:
        rows = session.scalars(select(Teacher).order_by(Teacher.name)).all()
        return [NamedItem(id=r.id, name=r.name) for r in rows]

    @app.get("/api/subjects")
    def list_subjects(session: SessionDep) -> list[NamedItem]:
        rows = session.scalars(select(Subject).order_by(Subject.name)).all()
        return [NamedItem(id=r.id, name=r.name) for r in rows]

    @app.get("/api/sections")
    def list_sections(session: SessionDep) -> list[NamedItem]:
        rows = session.scalars(select(Section).order_by(Section.name)).all()
        return [NamedItem(id=r.id, name=r.name) for r in rows]

    # ---- lectures and manual sessions -------------------------------------------

    @app.get("/api/lectures")
    def list_lectures(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        room_id: str | None = None,
    ) -> list[LectureOut]:
        """Lectures, newest first."""
        query = select(Lecture).order_by(Lecture.starts_at.desc(), Lecture.id.desc()).limit(limit)
        if room_id is not None:
            query = query.where(Lecture.room_id == room_id)
        return [LectureOut.from_row(r) for r in session.scalars(query).unique()]

    @app.get("/api/sessions")
    def list_sessions(
        session: SessionDep,
        open_only: Annotated[bool | None, Query(alias="open")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[LectureOut]:
        """Manual sessions, newest first. `open=true` lists only the ones still running."""
        query = (
            select(Lecture)
            .where(Lecture.source == MANUAL)
            .order_by(Lecture.starts_at.desc(), Lecture.id.desc())
            .limit(limit)
        )
        if open_only is True:
            query = query.where(Lecture.ends_at.is_(None))
        elif open_only is False:
            query = query.where(Lecture.ends_at.is_not(None))
        return [LectureOut.from_row(r) for r in session.scalars(query).unique()]

    @app.post("/api/sessions", status_code=201)
    def start_session(body: SessionStart, session: SessionDep) -> LectureOut:
        """Start a manual session. It overrides the timetable in that room until stopped."""
        if session.get(Room, body.room_id) is None:
            raise HTTPException(404, f"Unknown room: {body.room_id}")
        if session.get(Teacher, body.teacher_id) is None:
            raise HTTPException(404, f"Unknown teacher id: {body.teacher_id}")
        if session.get(Subject, body.subject_id) is None:
            raise HTTPException(404, f"Unknown subject id: {body.subject_id}")
        if body.section_id is not None and session.get(Section, body.section_id) is None:
            raise HTTPException(404, f"Unknown section id: {body.section_id}")
        conflict = HTTPException(409, f"Room {body.room_id} already has an open session")
        open_in_room = session.scalar(
            select(Lecture.id).where(
                Lecture.room_id == body.room_id,
                Lecture.source == MANUAL,
                Lecture.ends_at.is_(None),
            )
        )
        if open_in_room is not None:
            raise conflict
        now = clock()
        lecture = Lecture(
            source=MANUAL,
            room_id=body.room_id,
            teacher_id=body.teacher_id,
            subject_id=body.subject_id,
            section_id=body.section_id,
            lecture_date=now.astimezone(tz).date(),
            starts_at=now,
        )
        session.add(lecture)
        try:
            session.commit()  # the partial unique index also guards against a race
        except IntegrityError as e:
            session.rollback()
            raise conflict from e
        session.refresh(lecture)
        return LectureOut.from_row(lecture)

    @app.post("/api/sessions/{session_id}/stop")
    def stop_session(session_id: int, session: SessionDep) -> LectureOut:
        lecture = session.get(Lecture, session_id)
        if lecture is None or lecture.source != MANUAL:
            raise HTTPException(404, f"Unknown session id: {session_id}")
        if lecture.ends_at is not None:
            raise HTTPException(409, f"Session {session_id} is already stopped")
        lecture.ends_at = clock()
        session.commit()
        session.refresh(lecture)
        return LectureOut.from_row(lecture)

    return app
