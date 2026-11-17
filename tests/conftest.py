from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from backend.db import ensure_database_exists
from backend.main import create_app
from backend.models import Base
from shared.config import test_database_url


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The backend running against TEST_DATABASE_URL, starting from clean tables.

    Skips when TEST_DATABASE_URL is not set (for example before .env exists).
    """
    url = test_database_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set (create .env from .env.example)")
    ensure_database_exists(url)
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    # The app start-up recreates the tables and seeds R101, CAM1 and STU001.
    with TestClient(create_app(url)) as test_client:
        yield test_client
