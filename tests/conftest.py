"""Common fixtures: a temporary file-backed SQLite database."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import menu.db.connection
from menu.db.database import initialise

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.fixture
def db_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A temp-file SQLite engine with the schema created.

    It is also installed as the default engine, so actions that open
    their own session (via get_session) operate on the same database.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(menu.db.connection, "default_engine", engine)
    initialise(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(db_engine: Engine) -> Iterator[Session]:
    """A session on the test database."""
    with Session(db_engine) as db_session:
        yield db_session
