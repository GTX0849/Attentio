"""ensure_database_exists on a real server: use the database that is there, create only what is missing."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url

from backend.db import ensure_database_exists
from shared.config import test_database_url as configured_test_url


@pytest.fixture
def url() -> str:
    found = configured_test_url()
    if found is None:
        pytest.skip("TEST_DATABASE_URL is not set (create .env from .env.example)")
    return found


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Every SQL statement any engine runs while the test is going."""
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    yield seen
    event.remove(Engine, "before_cursor_execute", record)


def database_exists(url: str, name: str) -> bool:
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            return bool(conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar())
    finally:
        admin.dispose()


def drop_database(url: str, name: str) -> None:
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        admin.dispose()


def test_a_database_that_already_exists_is_used_without_trying_to_create_it(
    url: str, statements: list[str]
) -> None:
    ensure_database_exists(url)  # the test database may not exist yet; this makes sure it does
    statements.clear()
    ensure_database_exists(url)
    assert not [s for s in statements if "CREATE DATABASE" in s.upper()]


def test_a_postgresql_url_from_a_managed_host_works_without_the_plus_psycopg_part(
    url: str, statements: list[str]
) -> None:
    ensure_database_exists(url)
    plain = url.replace("postgresql+psycopg://", "postgresql://")
    assert plain != url
    statements.clear()
    ensure_database_exists(plain)
    assert not [s for s in statements if "CREATE DATABASE" in s.upper()]


def test_a_missing_database_is_created(url: str) -> None:
    name = f"classpulse_missing_{uuid.uuid4().hex[:8]}"
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    try:
        assert not database_exists(url, name)
        ensure_database_exists(target)
        assert database_exists(url, name)
    finally:
        drop_database(url, name)


def test_a_missing_database_the_user_may_not_create_gives_a_clear_error_without_the_password(url: str) -> None:
    role = f"cp_limited_{uuid.uuid4().hex[:8]}"
    name = f"classpulse_missing_{uuid.uuid4().hex[:8]}"
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"CREATE ROLE {role} LOGIN NOCREATEDB PASSWORD 'hunter2-secret'"))
        target = make_url(url).set(username=role, password="hunter2-secret", database=name)
        with pytest.raises(RuntimeError) as error:
            ensure_database_exists(target.render_as_string(hide_password=False))
        message = str(error.value)
        assert name in message and "does not exist" in message and "must already exist" in message
        assert "hunter2-secret" not in message
        assert not database_exists(url, name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def test_a_user_who_may_only_connect_to_its_own_database_can_use_it(url: str) -> None:
    """The managed-host case: the database exists, and the user cannot even open the `postgres` one."""
    role = f"cp_limited_{uuid.uuid4().hex[:8]}"
    ensure_database_exists(url)
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"CREATE ROLE {role} LOGIN NOCREATEDB PASSWORD 'pw-for-test'"))
            conn.execute(text(f"REVOKE CONNECT ON DATABASE postgres FROM PUBLIC, {role}"))
        target = make_url(url).set(username=role, password="pw-for-test")
        ensure_database_exists(target.render_as_string(hide_password=False))
    finally:
        with admin.connect() as conn:
            conn.execute(text("GRANT CONNECT ON DATABASE postgres TO PUBLIC"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
