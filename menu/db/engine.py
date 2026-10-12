"""Scratch database engine.

The database lives on the Postgres 18 server from docker-compose.yml
(`docker compose up -d db`): it is throwaway experiment storage,
rebuilt from scratch whenever the schema changes.
"""

import os
from typing import TYPE_CHECKING

from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5433/menu"


def database_url() -> str:
    """The configured database URL: env DATABASE_URL, else the default
    compose stack on localhost:5433."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None) -> Engine:
    """Build an engine for `url` (default: `database_url()`); the pool
    pre-pings connections so a server restart doesn't surface as a
    stale-connection error."""
    return create_engine(url if url is not None else database_url(), pool_pre_ping=True)


engine = make_engine(database_url())
