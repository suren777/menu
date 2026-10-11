"""Scratch SQLite engine.

The database file lives at the project root (gitignored), not inside
the package: it is throwaway experiment storage, rebuilt from scratch
whenever the schema changes.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, event

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

DB_PATH = Path(__file__).resolve().parents[2] / "database.db"

DEFAULT_DATABASE_URL = f"sqlite:///{DB_PATH}"


def database_url() -> str:
    """The configured database URL: env DATABASE_URL, else the default
    SQLite file URL (still the default — the Postgres switch is a
    separate decision)."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


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


def make_engine(url: str | None = None) -> Engine:
    """Build an engine for `url` (default: `database_url()`), wiring the
    SQLite pragmas only when the URL's dialect is sqlite — a Postgres
    connection would choke on them."""
    resolved = url if url is not None else database_url()
    engine = create_engine(resolved)
    if engine.dialect.name == "sqlite":
        configure_sqlite(engine)
    return engine


engine = make_engine(database_url())
