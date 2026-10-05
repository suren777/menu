"""Scratch SQLite engine.

The database file lives at the project root (gitignored), not inside
the package: it is throwaway experiment storage, rebuilt from scratch
whenever the schema changes.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, event

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

DB_PATH = Path(__file__).resolve().parents[2] / "database.db"

engine = create_engine(f"sqlite:///{DB_PATH}")


def _sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    for pragma in (
        "PRAGMA foreign_keys=ON",
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
    ):
        cursor.execute(pragma)
    cursor.close()


def configure_sqlite(target: Engine) -> None:
    """Make the database enforce what the schema's docstrings promise:
    foreign keys are checked, not decorative — plus the WAL settings a
    single-process lab wants."""
    event.listen(target, "connect", _sqlite_pragmas)


configure_sqlite(engine)
