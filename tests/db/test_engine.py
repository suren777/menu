"""The pragma guardrails: the database enforces what the docstrings
promise, on every connection, not just in code — and `make_engine`
applies them only where they mean something (sqlite)."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, event, text

from menu.db.engine import (
    DEFAULT_DATABASE_URL,
    _sqlite_pragmas,
    configure_sqlite,
    database_url,
    make_engine,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from sqlalchemy.engine import Engine

POSTGRES_URL = "postgresql+psycopg://menu:menu@localhost:5432/menu"


def test_configure_sqlite_turns_the_pragmas_on(tmp_path: Path) -> None:
    engine: Engine = create_engine(f"sqlite:///{tmp_path}/pragmas.db")
    configure_sqlite(engine)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA synchronous")).scalar() == 1
    finally:
        engine.dispose()


def test_make_engine_sets_the_pragmas_for_a_sqlite_url(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path}/pragmas.db")
    assert event.contains(engine, "connect", _sqlite_pragmas)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()


def test_make_engine_registers_no_sqlite_listener_for_postgres() -> None:
    # Created only: a Postgres server is not part of this suite.
    engine = make_engine(POSTGRES_URL)
    try:
        assert engine.dialect.name == "postgresql"
        assert not event.contains(engine, "connect", _sqlite_pragmas)
    finally:
        engine.dispose()


def test_database_url_env_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    assert database_url() == POSTGRES_URL
    engine = make_engine()
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()
    monkeypatch.delenv("DATABASE_URL")
    assert database_url() == DEFAULT_DATABASE_URL
