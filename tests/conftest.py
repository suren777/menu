from collections.abc import Generator

from pytest import fixture
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from menu.db.connection import get_session
from menu.db.database import initialise

test_engine = create_engine("sqlite:///:memory:", echo=True)


def pytest_configure():
    initialise(test_engine)


@fixture
def session() -> Generator[Session, None, None]:
    initialise(test_engine)
    with get_session(test_engine) as session:
        yield session