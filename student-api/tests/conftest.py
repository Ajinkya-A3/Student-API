import os

# Settings.DB_NAME / DB_USER / DB_PASSWORD have no defaults, so they must
# exist before any `app.*` module (which triggers app.config.settings) is
# imported. These values are never actually connected to - the real test
# DB is the separate SQLite engine built below - they only need to exist
# so Settings() doesn't raise a validation error at import time.
os.environ.setdefault("DB_HOST", "unused")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.db import get_db
from app.main import app

# A single shared in-memory SQLite connection for the whole test run.
# StaticPool is required so every session (including the ones FastAPI
# opens from its threadpool for sync endpoints) reuses the same
# in-memory database instead of each getting a blank one.
engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(scope="function", autouse=True)
def _reset_database():
    """
    Fresh schema for every test function so tests never leak state
    (e.g. the unique-email constraint) into one another.
    """
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def student_payload():
    return {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada.lovelace@example.com",
        "age": 28,
    }