"""FastAPI app: ingests attention records, serves recent windows, timetable and lectures."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import Engine, func, literal_column, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.dml import ReturningInsert

from backend import (
    auth_routes,
    marks,
    marks_service,
    metrics,
    simulation,
    student_profile,
    teacher_feedback,
    teacher_figures,
)
from backend.access import NO_ACCESS, Access, Principal, SessionDep, check_teacher
from backend.clock import Clock, resolve_timezone, system_clock
from backend.db import init_database
from backend.lectures import MANUAL, LectureResolver
from backend.marks_file import MarksFileError
from backend.models import (
    Account,
    AttentionRecordRow,
    Camera,
    Lecture,
    MarksTest,
    Room,
    Section,
    Student,
    Subject,
    Teacher,
    TimetableSlot,
)
from backend.schemas import (
    AdminOverview,
    CameraOut,
    CameraRegistration,
    CurrentResponse,
    GroupsResponse,
    IngestResult,
    LectureOut,
    LiveResponse,
    LiveWindow,
    MarksTestOut,
    MarksUploadResult,
    NamedItem,
    ProfileResponse,
    RoomOut,
    RowErrorOut,
    SessionStart,
    SimulationIn,
    SimulationSummary,
    SlotOut,
    StudentCreate,
    StudentMarksResponse,
    StudentOut,
    TeacherFeedback,
    TeacherSummary,
    TimelineResponse,
    TimetableOut,
    TimetableUploadResult,
    WipeResult,
)
from backend.timetable import TimetableFileError, replace_timetable
from shared.config import database_url, load_config, service_key as configured_service_key
from shared.schema import AttentionRecord as AttentionRecordIn

MAX_UPLOAD_BYTES = 1_000_000


log = logging.getLogger("backend.auth")


def create_app(
    url: str | None = None,
    *,
    timezone: str | None = None,
    clock: Clock = system_clock,
    window_seconds: int | None = None,
    auth_required: bool = True,
    service_key: str | None = None,
) -> FastAPI:
    """Build the app.

    `url` defaults to DATABASE_URL from .env. `timezone` is the IANA zone the
    timetable is written in (default: CLASSPULSE_TIMEZONE, else Asia/Kolkata).
    `clock` and `window_seconds` exist so tests can control time; the window
    length defaults to the one in config/classpulse.yaml.

    `auth_required` (default True) makes every endpoint check who is calling; see backend/access.py
    and the README. False turns every check into a no-op and exists for the logic tests. `service_key`
    is the key the worker and simulator send as X-ClassPulse-Key; None reads CLASSPULSE_SERVICE_KEY,
    and "" means none (then only an admin token passes the machine endpoints).
    """
    tz: ZoneInfo = resolve_timezone(timezone)  # fail at start-up on a bad zone name
    window_len = window_seconds if window_seconds is not None else load_config().window_seconds
    min_presence = metrics.min_presence_ratio()
    teacher_settings = teacher_figures.teacher_settings()
    marks_settings = marks.marks_settings()
    machine_key = service_key if service_key is not None else configured_service_key()
    access = Access(required=auth_required, service_key=machine_key, clock=clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = init_database(url or database_url())
        app.state.engine = engine
        if auth_required:
            _log_sign_in_state(engine, bool(machine_key))
        yield
        engine.dispose()

    app = FastAPI(title="ClassPulse backend", lifespan=lifespan)
    app.include_router(auth_routes.build_router(access, clock))

    # ---- records -------------------------------------------------------------

    @app.post("/api/records", dependencies=[Depends(access.admin_or_service)])
    def post_records(records: list[AttentionRecordIn], session: SessionDep) -> IngestResult:
        # The sender's window_start is trusted. Check every reference first so a
        # bad record rejects the whole batch instead of storing part of it.
        sent_students = {r.student_id for r in records}
        known_students = set(
            session.scalars(select(Student.register_no).where(Student.register_no.in_(sent_students)))
        )
        unknown_students = sorted(sent_students - known_students)
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
        resolver = LectureResolver(session, window_len, tz)

        def lecture_for(r: AttentionRecordIn) -> int | None:
            start = r.window_start.astimezone(UTC)
            if (r.student_id, start) in already_assigned:
                return None  # the stored lecture is kept by the upsert below
            return resolver.resolve(cameras[r.camera_id].room_id, start)

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

    @app.get("/api/students/{register_no}/live", dependencies=[Depends(access.student_self)])
    def get_live(
        register_no: str,
        session: SessionDep,
        minutes: Annotated[int, Query(ge=1, le=24 * 60)] = 10,
    ) -> LiveResponse:
        student = session.get(Student, register_no)
        if student is None:
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
            simulated=student.simulated,
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

    @app.post("/api/students", status_code=201, dependencies=[Depends(access.admin_or_service)])
    def create_student(body: StudentCreate, session: SessionDep) -> StudentOut:
        """Register a student, optionally in a section (created, with the same simulated flag, if new)."""
        if session.get(Student, body.register_no) is not None:
            raise HTTPException(409, f"Student {body.register_no} already exists")
        section: Section | None = None
        if body.section is not None:
            section = session.scalar(select(Section).where(Section.name == body.section))
            if section is None:
                section = Section(name=body.section, simulated=body.simulated)
                session.add(section)
                session.flush()
        student = Student(
            register_no=body.register_no,
            name=body.name,
            section_id=section.id if section else None,
            simulated=body.simulated,
            archetype=body.archetype,
        )
        session.add(student)
        session.commit()
        return StudentOut.from_row(student)

    @app.get("/api/students", dependencies=[Depends(access.admin_or_service)])
    def list_students(session: SessionDep) -> list[StudentOut]:
        """Every student, live ones first, then by register number."""
        rows = session.scalars(select(Student).order_by(Student.simulated, Student.register_no))
        return [StudentOut.from_row(r) for r in rows.unique()]

    @app.get("/api/students/{register_no}/profile", dependencies=[Depends(access.student_self)])
    def get_profile(register_no: str, session: SessionDep) -> ProfileResponse:
        """Attention by subject, teacher, hour and week, attendance per lecture and this month's count.

        Built only from this student's records; see backend/student_profile.py.
        """
        if session.get(Student, register_no) is None:
            raise HTTPException(404, f"Unknown student register number: {register_no}")
        return student_profile.build_profile(session, register_no, clock(), tz, min_presence)

    @app.get("/api/students/{register_no}/current", dependencies=[Depends(access.student_self)])
    def get_current(register_no: str, session: SessionDep) -> CurrentResponse:
        """The live header: running lecture, last 5 minutes, overall average, presence and distracted time."""
        if session.get(Student, register_no) is None:
            raise HTTPException(404, f"Unknown student register number: {register_no}")
        return student_profile.build_current(session, register_no, clock(), tz, min_presence)

    @app.get("/api/students/{register_no}/marks", dependencies=[Depends(access.student_self)])
    def get_student_marks(register_no: str, session: SessionDep) -> StudentMarksResponse:
        """This student's own marks and percentile in each test, newest first. Nothing about other students."""
        student = session.get(Student, register_no)
        if student is None:
            raise HTTPException(404, f"Unknown student register number: {register_no}")
        return marks_service.student_marks(session, student)

    # ---- timetable -----------------------------------------------------------

    @app.post("/api/timetable", dependencies=[Depends(access.admin_or_service)])
    def upload_timetable(
        session: SessionDep,
        file: Annotated[UploadFile, File()],
        simulated: Annotated[bool, Form()] = False,
    ) -> TimetableUploadResult:
        """Replace the template. `simulated=true` replaces and creates only simulated slots and entities."""
        raw = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file is larger than 1 MB")
        try:
            result = replace_timetable(session, raw, simulated)
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

    @app.get("/api/timetable", dependencies=[Depends(access.admin_or_service)])
    def get_timetable(session: SessionDep) -> TimetableOut:
        slots = session.scalars(
            select(TimetableSlot).order_by(
                TimetableSlot.day_of_week, TimetableSlot.start_time, TimetableSlot.room_id
            )
        ).all()
        return TimetableOut(timezone=tz.key, slots=[SlotOut.from_row(s) for s in slots])

    # ---- rooms, cameras and pickers -------------------------------------------

    @app.get("/api/rooms", dependencies=[Depends(access.admin_or_service)])
    def list_rooms(session: SessionDep) -> list[RoomOut]:
        rooms = session.scalars(select(Room).order_by(Room.id)).all()
        return [
            RoomOut(id=r.id, cameras=sorted(c.id for c in r.cameras), simulated=r.simulated)
            for r in rooms
        ]

    @app.put("/api/cameras/{camera_id}", dependencies=[Depends(access.admin_or_service)])
    def register_camera(
        camera_id: str, body: CameraRegistration, session: SessionDep
    ) -> CameraOut:
        """Put a camera in a room, creating the camera and the room when new."""
        room = session.get(Room, body.room_id)
        room_created = room is None
        if room is None:
            session.add(Room(id=body.room_id, simulated=body.simulated))
            session.flush()
        camera = session.get(Camera, camera_id)
        camera_created = camera is None
        if camera is None:
            camera = Camera(id=camera_id, room_id=body.room_id, simulated=body.simulated)
            session.add(camera)
        else:
            camera.room_id = body.room_id
        session.commit()
        return CameraOut(
            id=camera_id,
            room_id=body.room_id,
            camera_created=camera_created,
            room_created=room_created,
            simulated=camera.simulated,
        )

    @app.get("/api/teachers", dependencies=[Depends(access.staff_or_service)])
    def list_teachers(session: SessionDep) -> list[NamedItem]:
        rows = session.scalars(select(Teacher).order_by(Teacher.name)).all()
        return [NamedItem(id=r.id, name=r.name, simulated=r.simulated) for r in rows]

    @app.get("/api/teachers/{teacher_id}/subjects", dependencies=[Depends(access.teacher_self)])
    def teacher_subjects(teacher_id: int, session: SessionDep) -> list[NamedItem]:
        """The subjects this teacher is timetabled for (the marks upload preselects the first)."""
        rows = session.scalars(
            select(Subject)
            .join(TimetableSlot, TimetableSlot.subject_id == Subject.id)
            .where(TimetableSlot.teacher_id == teacher_id)
            .distinct()
            .order_by(Subject.name)
        ).all()
        return [NamedItem(id=r.id, name=r.name, simulated=r.simulated) for r in rows]

    @app.get("/api/teachers/feedback", dependencies=[Depends(access.admin)])
    def all_teachers_feedback(session: SessionDep) -> list[TeacherSummary]:
        """Every teacher's figures (self-comparison, slot-adjusted, ranges) and attendance, by name.

        For the admin view. Worked out in one pass; see backend/teacher_figures.py.
        """
        return teacher_feedback.build_all(session, clock(), tz, teacher_settings, min_presence)

    @app.get("/api/admin/overview", dependencies=[Depends(access.admin)])
    def admin_overview(session: SessionDep) -> AdminOverview:
        """The admin view in one answer: every teacher's figures (with the plain class average),
        attendance by section and the number of unassigned record windows.

        Totals per teacher and per section only; no figure for any individual student.
        """
        return teacher_feedback.build_overview(session, clock(), tz, teacher_settings, min_presence)

    @app.get("/api/teachers/{teacher_id}/feedback", dependencies=[Depends(access.teacher_self)])
    def get_teacher_feedback(teacher_id: int, session: SessionDep) -> TeacherFeedback:
        """One teacher's figures, lectures (newest first) and attendance. Nothing about other teachers."""
        teacher = session.get(Teacher, teacher_id)
        if teacher is None:
            raise HTTPException(404, f"Unknown teacher id: {teacher_id}")
        return teacher_feedback.build_one(session, teacher, clock(), tz, teacher_settings, min_presence)

    # ---- marks ----------------------------------------------------------------------

    @app.post("/api/marks/tests")
    def upload_marks(
        session: SessionDep,
        who: Annotated[Principal, Depends(access.staff_or_service)],
        file: Annotated[UploadFile, File()],
        teacher_id: Annotated[int, Form()],
        subject_id: Annotated[int, Form()],
        test_date: Annotated[date, Form()],
        name: Annotated[str, Form()] = "",
        simulated: Annotated[bool, Form()] = False,
    ) -> MarksUploadResult:
        """Upload the marks of one test (CSV or .xlsx: register_no, marks, max_marks) for a teacher and subject.

        All or nothing: any invalid row or unknown register number is reported by line and nothing is
        stored. Uploading the same teacher, subject, date and name again replaces that test's marks.
        A test is simulated when `simulated` is true or its teacher is.
        """
        check_teacher(who, teacher_id, also_admin=True, also_service=True)  # a teacher uploads their own
        teacher = session.get(Teacher, teacher_id)
        if teacher is None:
            raise HTTPException(404, f"Unknown teacher id: {teacher_id}")
        subject = session.get(Subject, subject_id)
        if subject is None:
            raise HTTPException(404, f"Unknown subject id: {subject_id}")
        test_name = marks_service.clean_name(name)
        if len(test_name) > marks_service.MAX_NAME_LENGTH:
            raise HTTPException(422, f"The test name is longer than {marks_service.MAX_NAME_LENGTH} characters")
        raw = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file is larger than 1 MB")
        request = marks_service.UploadRequest(teacher, subject, test_date, test_name, simulated)
        try:
            return marks_service.upload_marks(session, raw, file.filename or "", request)
        except MarksFileError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/teachers/{teacher_id}/marks/tests", dependencies=[Depends(access.teacher_self)])
    def list_teacher_tests(teacher_id: int, session: SessionDep) -> list[MarksTestOut]:
        """The teacher's own tests, newest first."""
        if session.get(Teacher, teacher_id) is None:
            raise HTTPException(404, f"Unknown teacher id: {teacher_id}")
        return marks_service.teacher_tests(session, teacher_id)

    @app.get("/api/marks/tests/{test_id}/groups")
    def get_marks_groups(
        test_id: int,
        session: SessionDep,
        who: Annotated[Principal, Depends(access.caller)],
        students: Annotated[bool, Query()] = False,
    ) -> GroupsResponse:
        """The four student groups of a test (attention over the lectures it covers by marks).

        Counts always; with `students=true` also each student's marks, attention and group, which only
        the teacher's own view asks for. Only the teacher who ran the test may read it.
        """
        test = session.get(MarksTest, test_id)
        if test is None:
            if who.kind != "open" and who.role != "teacher":
                raise HTTPException(403, NO_ACCESS)
            raise HTTPException(404, f"Unknown test id: {test_id}")
        check_teacher(who, test.teacher_id)
        return marks_service.build_groups(session, test, min_presence, marks_settings, students)

    @app.get("/api/lectures/{lecture_id}/timeline")
    def get_lecture_timeline(
        lecture_id: int, session: SessionDep, who: Annotated[Principal, Depends(access.caller)]
    ) -> TimelineResponse:
        """Class attention per 30-second window of one lecture, counted from its start. Only for its teacher."""
        lecture = session.get(Lecture, lecture_id)
        if lecture is None:
            if who.kind != "open" and who.role != "teacher":
                raise HTTPException(403, NO_ACCESS)
            raise HTTPException(404, f"Unknown lecture id: {lecture_id}")
        check_teacher(who, lecture.teacher_id)
        return teacher_feedback.build_timeline(session, lecture, window_len)

    @app.get("/api/subjects", dependencies=[Depends(access.staff_or_service)])
    def list_subjects(session: SessionDep) -> list[NamedItem]:
        rows = session.scalars(select(Subject).order_by(Subject.name)).all()
        return [NamedItem(id=r.id, name=r.name, simulated=r.simulated) for r in rows]

    @app.get("/api/sections", dependencies=[Depends(access.staff_or_service)])
    def list_sections(session: SessionDep) -> list[NamedItem]:
        rows = session.scalars(select(Section).order_by(Section.name)).all()
        return [NamedItem(id=r.id, name=r.name, simulated=r.simulated) for r in rows]

    # ---- lectures and manual sessions -------------------------------------------

    @app.get("/api/lectures", dependencies=[Depends(access.admin_or_service)])
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

    @app.get("/api/sessions", dependencies=[Depends(access.admin_or_service)])
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

    @app.post("/api/sessions", status_code=201, dependencies=[Depends(access.admin)])
    def start_session(body: SessionStart, session: SessionDep) -> LectureOut:
        """Start a manual session. It overrides the timetable in that room until stopped."""
        room = session.get(Room, body.room_id)
        if room is None:
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
            simulated=room.simulated,
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

    @app.post("/api/sessions/{session_id}/stop", dependencies=[Depends(access.admin)])
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

    # ---- simulation -------------------------------------------------------------------

    @app.put("/api/simulation", dependencies=[Depends(access.admin_or_service)])
    def put_simulation(body: SimulationIn, session: SessionDep) -> SimulationSummary | None:
        """Store the built-in (true) effects of the simulation, replacing any stored before."""
        simulation.store_effects(session, body)
        return simulation.summary(session)

    @app.get("/api/simulation")
    def get_simulation(
        session: SessionDep, who: Annotated[Principal, Depends(access.caller)]
    ) -> SimulationSummary | None:
        """What the loaded simulation holds, with its true effects; null when none is loaded.

        Every signed-in role gets the counts (for the "simulated data" banner); only an admin or the
        service key also gets the true effects and the archetypes, which would give away other
        teachers' standing.
        """
        summary = simulation.summary(session)
        if summary is not None and not (who.is_admin or who.is_service):
            summary = summary.model_copy(update={"effects": [], "archetypes": {}})
        return summary

    @app.delete("/api/simulation", dependencies=[Depends(access.admin_or_service)])
    def delete_simulation(session: SessionDep) -> WipeResult:
        """Remove all simulated data and the stored effects. Live data is not touched."""
        return simulation.wipe(session)

    return app


def _log_sign_in_state(engine: Engine, has_service_key: bool) -> None:
    """Say at start-up what is missing: the first account and the service key."""
    with Session(engine) as session:
        if session.scalar(select(func.count()).select_from(Account)) == 0:
            log.warning(
                "No accounts yet, so nobody can sign in. Create the first admin with: "
                "python -m backend.accounts create --role admin --email admin@example.edu"
            )
    if not has_service_key:
        log.warning(
            "CLASSPULSE_SERVICE_KEY is not set: the worker and the simulator cannot send records "
            "unless they use an admin token. Set the same key for the backend and for them (see README)."
        )
