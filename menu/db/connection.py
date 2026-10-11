"""Session factories for the scratch database.

The default engine is resolved at call time (not bound into a default
argument at import time) so tests can patch it. An explicit engine
argument wins over everything; `use_engine` scopes a rebuild target
for a block; otherwise the module default applies.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from menu.db.engine import engine as default_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_override: ContextVar[Engine | None] = ContextVar("engine_override", default=None)


def current_engine(engine: Engine | None = None) -> Engine:
    """The engine to bind to: explicit argument > `use_engine` override
    > the module default (resolved at call time so tests can patch it)."""
    return engine or _override.get() or default_engine


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
