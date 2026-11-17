"""FastAPI app: ingests attention records and serves recent windows."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import Engine, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.dml import ReturningInsert
from sqlalchemy.orm import Session

from backend import metrics
from backend.db import init_database
from backend.models import AttentionRecordRow, Camera, Student
from shared.config import database_url
from shared.schema import AttentionRecord


class IngestResult(BaseModel):
    received: int  # records in the request
    inserted: int  # new rows
    updated: int  # existing (student, window_start) rows that were replaced


class LiveWindow(BaseModel):
    window_start: datetime
    attentive: float
    neutral: float
    distracted: float
    not_visible: float
    attention: float | None
    presence: float | None


class LiveResponse(BaseModel):
    register_no: str
    minutes: int
    windows: list[LiveWindow]  # oldest first


def get_session(request: Request) -> Iterator[Session]:
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(url: str | None = None) -> FastAPI:
    """Build the app. `url` defaults to DATABASE_URL from .env."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = init_database(url or database_url())
        app.state.engine = engine
        yield
        engine.dispose()

    app = FastAPI(title="ClassPulse backend", lifespan=lifespan)

    @app.post("/api/records")
    def post_records(records: list[AttentionRecord], session: SessionDep) -> IngestResult:
        # The sender's window_start is trusted. Check every reference first so a
        # bad record rejects the whole batch instead of storing part of it.
        unknown_students = sorted(
            {r.student_id for r in records if session.get(Student, r.student_id) is None}
        )
        if unknown_students:
            raise HTTPException(404, f"Unknown student register number(s): {unknown_students}")
        unknown_cameras = sorted(
            {r.camera_id for r in records if session.get(Camera, r.camera_id) is None}
        )
        if unknown_cameras:
            raise HTTPException(404, f"Unknown camera id(s): {unknown_cameras}")

        # A repeated (student, window_start) inside one batch: the last one wins.
        latest = {(r.student_id, r.window_start): r for r in records}
        if not latest:
            return IngestResult(received=0, inserted=0, updated=0)

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
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        rows = session.scalars(
            select(AttentionRecordRow)
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
                )
                for r in rows
            ],
        )

    return app
