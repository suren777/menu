from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from menu.db.engine import engine


@contextmanager
def get_session(engine: Engine = engine) -> Generator[Session, Any, Any]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@contextmanager
def get_ro_session(engine: Engine = engine) -> Generator[Session, None, None]:
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
