"""Common fixtures: an ephemeral Postgres 18 per pytest session.

One testcontainers `postgres:18-alpine` container serves the whole
session (never one container per test — parallel pytest runs time out
otherwise). The schema is created once on a template database and
every test clones it with `CREATE DATABASE ... TEMPLATE`, so tests
run isolated on real Postgres without paying the schema cost each
time.
"""

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from menu.db.connection import use_engine
from menu.db.database import initialise
from menu.db.engine import make_engine
from menu.domain import suggestions
from menu.ingest import review_inputs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

TEMPLATE_DATABASE = "menu_template"


def url_for(container: PostgresContainer, database: str) -> str:
    """The container's server URL, pointed at `database`."""
    return (
        make_url(container.get_connection_url())
        .set(database=database)
        .render_as_string(hide_password=False)
    )


def _admin_engine(container: PostgresContainer) -> Engine:
    """Autocommit engine for the CREATE/DROP DATABASE statements —
    they cannot run inside a transaction."""
    return create_engine(container.get_connection_url(), isolation_level="AUTOCOMMIT")


def _clone(container: PostgresContainer, template: str, name: str) -> Engine:
    """A fresh database cloned from the template, with an engine on it."""
    admin = _admin_engine(container)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
    finally:
        admin.dispose()
    return make_engine(url_for(container, name))


def _drop(container: PostgresContainer, name: str) -> None:
    """Drop the database even with pooled connections still open; a
    no-op when it is already gone."""
    admin = _admin_engine(container)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def database_exists(container: PostgresContainer, name: str) -> bool:
    """Whether `name` exists on the container's server."""
    admin = _admin_engine(container)
    try:
        with admin.connect() as conn:
            return (
                conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": name},
                ).scalar()
                is not None
            )
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """The one postgres:18-alpine container for the whole session."""
    container = PostgresContainer("postgres:18-alpine", driver="psycopg")
    try:
        container.start()
    except Exception as exc:  # noqa: BLE001 - any docker failure, one clear message
        pytest.fail(
            "Docker is required to run the test suite: the tests need an "
            "ephemeral postgres:18-alpine (testcontainers), and starting "
            f"it failed: {exc}"
        )
    # The suite's live URL is the container's: runtime database_url()
    # callers (menu-rebuild) must see the same server as the fixtures.
    os.environ["DATABASE_URL"] = container.get_connection_url()
    yield container
    del os.environ["DATABASE_URL"]
    container.stop()


@pytest.fixture(scope="session")
def template_database(pg_container: PostgresContainer) -> str:
    """The schema, created once on a template database."""
    admin = _admin_engine(pg_container)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{TEMPLATE_DATABASE}"'))
    finally:
        admin.dispose()
    engine = make_engine(url_for(pg_container, TEMPLATE_DATABASE))
    try:
        initialise(engine)
    finally:
        engine.dispose()
    return TEMPLATE_DATABASE


@pytest.fixture(autouse=True)
def empty_suggestions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeding reads suggestions.json; tests start from an empty ledger
    so accepted entries committed to the real file can't change them."""
    monkeypatch.setattr(suggestions, "SUGGESTIONS_PATH", tmp_path / "suggestions.json")


@pytest.fixture(autouse=True)
def temp_embeddings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Embedding caches written by tests stay out of the real .cache."""
    monkeypatch.setattr(review_inputs, "EMBEDDINGS_DIR", tmp_path / "embeddings")


@pytest.fixture
def db_engine(
    template_database: str,
    pg_container: PostgresContainer,
) -> Iterator[Engine]:
    """A fresh database cloned from the template, dropped after the
    test.

    It is also installed as the default engine (use_engine), so actions
    that open their own session (via get_session) operate on the same
    database.
    """
    name = f"t_{uuid.uuid4().hex}"
    engine = _clone(pg_container, template_database, name)
    with use_engine(engine):
        yield engine
    engine.dispose()
    _drop(pg_container, name)


@pytest.fixture
def make_database(
    template_database: str, pg_container: PostgresContainer
) -> Iterator[Callable[[], Engine]]:
    """Extra fresh databases (cloned from the template) for tests that
    need more than the default one; dropped after the test."""
    names: list[str] = []
    engines: list[Engine] = []

    def factory() -> Engine:
        name = f"t_{uuid.uuid4().hex}"
        engine = _clone(pg_container, template_database, name)
        names.append(name)
        engines.append(engine)
        return engine

    yield factory
    for engine in engines:
        engine.dispose()
    for name in names:
        _drop(pg_container, name)


@pytest.fixture
def drop_database(
    pg_container: PostgresContainer,
) -> Callable[[str], None]:
    """Drop a database the test created itself (a rebuild target, say):
    the same teardown the fixture-owned databases get."""
    return lambda name: _drop(pg_container, name)


@pytest.fixture
def session(db_engine: Engine) -> Iterator[Session]:
    """A session on the test database."""
    with Session(db_engine) as db_session:
        yield db_session
