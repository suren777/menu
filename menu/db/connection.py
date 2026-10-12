"""Session factories for the scratch database.

An explicit engine argument wins over everything; `use_engine` scopes
a rebuild target or a test database for a block; otherwise the shared
default engine applies (built once, on first use).
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from menu.db.engine import default_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_override: ContextVar[Engine | None] = ContextVar("engine_override", default=None)


def current_engine(engine: Engine | None = None) -> Engine:
    """The engine to bind to: explicit argument > `use_engine` override
    > the shared default engine."""
    return engine or _override.get() or default_engine()


@contextmanager
def use_engine(engine: Engine) -> Iterator[None]:
    """Make `engine` the default for everything run in the block —
    every action deep in the call stack (pipeline, seeding, nutrition)
    that opens its own session via get_session — and only there."""
    token = _override.set(engine)
    try:
        yield
    finally:
        _override.reset(token)


@contextmanager
def get_session(engine: Engine | None = None) -> Iterator[Session]:
    """Session bound to one transaction: commits on success, rolls back
    if the body raises."""
    with Session(current_engine(engine)) as session, session.begin():
        yield session


@contextmanager
def get_ro_session(engine: Engine | None = None) -> Iterator[Session]:
    """Read-only session: nothing is committed."""
    with Session(current_engine(engine)) as session:
        yield session
