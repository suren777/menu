"""Common fixtures: a temporary file-backed SQLite database."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import menu.db.connection
from menu.db.database import initialise
from menu.db.engine import configure_sqlite
from menu.db.ingredients import suggestions

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.fixture(autouse=True)
def empty_suggestions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeding reads suggestions.json; tests start from an empty ledger
    so accepted entries committed to the real file can't change them."""
    monkeypatch.setattr(suggestions, "SUGGESTIONS_PATH", tmp_path / "suggestions.json")


@pytest.fixture
def db_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A temp-file SQLite engine with the schema created.

    It is also installed as the default engine, so actions that open
    their own session (via get_session) operate on the same database.
    Foreign keys are enforced, like the real engine.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    configure_sqlite(engine)
    monkeypatch.setattr(menu.db.connection, "default_engine", engine)
    initialise(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(db_engine: Engine) -> Iterator[Session]:
    """A session on the test database."""
    with Session(db_engine) as db_session:
        yield db_session
