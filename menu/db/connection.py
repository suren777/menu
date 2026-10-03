"""Session factories for the scratch database.

The default engine is resolved at call time (not bound into a default
argument at import time) so tests can patch it.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from menu.db.engine import engine as default_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


@contextmanager
def get_session(engine: Engine | None = None) -> Iterator[Session]:
    """Session bound to one transaction: commits on success, rolls back
    if the body raises."""
    with Session(engine or default_engine) as session, session.begin():
        yield session


@contextmanager
def get_ro_session(engine: Engine | None = None) -> Iterator[Session]:
    """Read-only session: nothing is committed."""
    with Session(engine or default_engine) as session:
        yield session
