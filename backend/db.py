"""Engine creation, database auto-creation and start-up seeding."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.models import Base, Camera, Room, Student


def ensure_database_exists(database_url: str) -> None:
    """Create the target database if missing, via the server's `postgres` database."""
    url = make_url(database_url)
    name = url.database
    if not name:
        raise ValueError("The database URL has no database name")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar()
            if not exists:
                quoted = admin.dialect.identifier_preparer.quote(name)
                conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        admin.dispose()


def init_database(database_url: str) -> Engine:
    """Create the database and tables if needed, seed demo data, return an engine."""
    ensure_database_exists(database_url)
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    with Session(engine) as session:
        seed(session)
    return engine


# Columns added after the first release. create_all never alters an existing table, so a
# database made by an older version gets them here (idempotent).
_ADDED_COLUMNS = [
    *(
        (table, "simulated BOOLEAN NOT NULL DEFAULT false")
        for table in (
            "rooms",
            "cameras",
            "students",
            "teachers",
            "subjects",
            "sections",
            "timetable_slots",
            "lectures",
        )
    ),
    ("students", "section_id INTEGER REFERENCES sections (id)"),
    ("students", "archetype VARCHAR"),
]


def add_missing_columns(engine: Engine) -> None:
    with engine.begin() as conn:
        for table, definition in _ADDED_COLUMNS:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {definition}"))


def seed(session: Session) -> None:
    """Idempotent demo data: room R101, camera CAM1, student STU001."""
    if session.get(Room, "R101") is None:
        session.add(Room(id="R101"))
        session.flush()
    if session.get(Camera, "CAM1") is None:
        session.add(Camera(id="CAM1", room_id="R101"))
    if session.get(Student, "STU001") is None:
        session.add(Student(register_no="STU001", name="Demo Student"))
    session.commit()
