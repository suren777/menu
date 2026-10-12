"""Connection tests: the use_engine override and its resolution order,
and the fixture teardown dropping its per-test databases."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

from menu.db.connection import current_engine, get_session, use_engine
from menu.db.engine import default_engine

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine
    from testcontainers.community.postgres import PostgresContainer


def test_current_engine_defaults_to_the_cached_engine() -> None:
    """No explicit engine and no use_engine override: the shared
    default engine, the same object on every call."""
    assert current_engine() is default_engine()


def test_use_engine_binds_get_session(
    db_engine: Engine, make_database: Callable[[], Engine]
) -> None:
    """Inside use_engine(e), get_session() binds to e without being
    told; after the block it is back to the default."""
    override = make_database()
    with use_engine(override), get_session() as session:
        assert session.get_bind() is override
    with get_session() as session:
        assert session.get_bind() is db_engine


def test_use_engine_resets_after_an_exception(
    db_engine: Engine, make_database: Callable[[], Engine]
) -> None:
    """The override does not leak when the body raises."""
    override = make_database()
    with pytest.raises(RuntimeError, match="boom"), use_engine(override):
        raise RuntimeError("boom")
    with get_session() as session:
        assert session.get_bind() is db_engine


def test_explicit_engine_beats_the_override(
    db_engine: Engine, make_database: Callable[[], Engine]
) -> None:
    """An explicit argument wins over use_engine."""
    explicit = make_database()
    with use_engine(explicit), get_session(db_engine) as session:
        assert session.get_bind() is db_engine


def test_per_test_databases_are_dropped_after_their_test(
    db_engine: Engine, pg_container: PostgresContainer
) -> None:
    """The fixture teardown drops its databases: the only t_ database
    this test can see is its own — every earlier test's is gone."""
    admin = create_engine(
        pg_container.get_connection_url(), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as conn:
            rows = conn.execute(text("SELECT datname FROM pg_database")).all()
            names = [name for (name,) in rows if name.startswith("t_")]
    finally:
        admin.dispose()
    assert names == [db_engine.url.database]
