"""Engine creation, database auto-creation and start-up seeding."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from backend.models import Base, Camera, Room, Student
from shared.config import normalise_database_url


def _is_missing_database(error: DBAPIError) -> bool:
    """True when a connection failed because the database named in the URL does not exist."""
    message = str(error.orig)
    return getattr(error.orig, "sqlstate", None) == "3D000" or ('database "' in message and "does not exist" in message)


def ensure_database_exists(database_url: str) -> None:
    """Make sure the database named in the URL exists.

    First connect to it. Only when the server says it does not exist is `CREATE DATABASE` tried, through
    the server's `postgres` database. A managed host (Neon, Render) makes the database for you and does
    not let you create another, so that case ends in a clear error instead of a permission failure.
    The error never contains the URL, which carries the password.
    """
    url = make_url(normalise_database_url(database_url))
    name = url.database
    if not name:
        raise ValueError("The database URL has no database name")
    probe = create_engine(url)
    try:
        with probe.connect():
            return
    except OperationalError as e:
        if not _is_missing_database(e):
            raise
    finally:
        probe.dispose()
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            quoted = admin.dialect.identifier_preparer.quote(name)
            conn.execute(text(f"CREATE DATABASE {quoted}"))
    except DBAPIError as e:
        raise RuntimeError(
            f'The database "{name}" does not exist and could not be created with this user. On a hosted '
            "database (Neon, Render) the database must already exist: copy the connection string of the "
            "database your provider created into DATABASE_URL."
        ) from e
    finally:
        admin.dispose()


def init_database(database_url: str) -> Engine:
    """Create the database and tables if needed, seed demo data, return an engine."""
    ensure_database_exists(database_url)
    # pool_pre_ping: a serverless database (Neon) closes idle connections, so test one before reuse.
    engine = create_engine(normalise_database_url(database_url), pool_pre_ping=True)
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
