"""Module with common fixtures for tests."""

from collections.abc import Generator

from pytest import fixture
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from menu.db.connection import get_session
from menu.db.database import initialise

test_engine = create_engine("sqlite:///:memory:", echo=True)


def pytest_configure():
    """Initialise the database for tests."""
    initialise(test_engine)


@fixture
def session() -> Generator[Session, None, None]:
    """Fixture for database session."""
    initialise(test_engine)
    with get_session(test_engine) as db_session:
        yield db_session
