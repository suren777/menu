from pytest import fixture
from sqlalchemy import create_engine
from menu.db.connection import get_session
from menu.db.database import initialise

test_engine = create_engine("sqlite:///:memory:", echo=True)


def pytest_configure():
    initialise()


@fixture
def session():
    initialise()
    return get_session(test_engine)
