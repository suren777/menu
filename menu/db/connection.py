from contextlib import contextmanager
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine
from menu.db.engine import engine


@contextmanager
def get_session(engine: Engine = engine):
    session = Session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@contextmanager
def get_ro_session(engine: Engine = engine):
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
