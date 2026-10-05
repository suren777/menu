"""The pragma guardrails: the database enforces what the docstrings
promise, on every connection, not just in code."""

from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text

from menu.db.engine import configure_sqlite

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


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
