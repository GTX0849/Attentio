from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from backend.db import ensure_database_exists
from backend.main import create_app
from backend.models import Base
from shared.config import test_database_url


ClientFactory = Callable[..., AbstractContextManager[TestClient]]


class FakeClock:
    """The backend's notion of "now". Starts at the real time and stands still until moved."""

    def __init__(self) -> None:
        self.current = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.current

    def set(self, moment: datetime) -> None:
        self.current = moment

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(clock: FakeClock) -> ClientFactory:
    """Build a backend (optionally in another time zone) on clean tables.

    Skips when TEST_DATABASE_URL is not set (for example before .env exists).
    """
    url = test_database_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set (create .env from .env.example)")

    @contextmanager
    def build(timezone: str = "Asia/Kolkata") -> Iterator[TestClient]:
        assert url is not None
        ensure_database_exists(url)
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
        # The app start-up recreates the tables and seeds R101, CAM1 and STU001.
        with TestClient(create_app(url, timezone=timezone, clock=clock, auth_required=False)) as test_client:
            yield test_client

    return build


SERVICE_KEY = "test-service-key-0123456789"


@pytest.fixture
def make_secure_client(clock: FakeClock) -> ClientFactory:
    """Like `make_client`, but with sign-in enforced (`auth_required=True`) and a service key set.

    `service_key=""` builds the app with no key configured.
    """
    url = test_database_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set (create .env from .env.example)")

    @contextmanager
    def build(timezone: str = "Asia/Kolkata", service_key: str = SERVICE_KEY) -> Iterator[TestClient]:
        assert url is not None
        ensure_database_exists(url)
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
        app = create_app(url, timezone=timezone, clock=clock, auth_required=True, service_key=service_key)
        with TestClient(app) as test_client:
            yield test_client

    return build


@pytest.fixture
def secure_client(make_secure_client: ClientFactory) -> Iterator[TestClient]:
    """The backend with sign-in enforced, on clean tables (only the seeded R101, CAM1 and STU001)."""
    with make_secure_client() as test_client:
        yield test_client


@pytest.fixture
def client(make_client: ClientFactory) -> Iterator[TestClient]:
    """The backend running against TEST_DATABASE_URL (Asia/Kolkata), starting from clean tables."""
    with make_client() as test_client:
        yield test_client
