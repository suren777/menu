"""The engine: DATABASE_URL wins over the default, and make_engine
builds a pooled engine for whatever URL it is given."""

from typing import TYPE_CHECKING

from menu.db.engine import DEFAULT_DATABASE_URL, database_url, make_engine

if TYPE_CHECKING:
    import pytest

POSTGRES_URL = "postgresql+psycopg://menu:menu@localhost:5432/menu"


def test_make_engine_targets_the_given_url_with_pre_ping() -> None:
    # Created only: no server is contacted until a connection is used.
    engine = make_engine(POSTGRES_URL)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.pool._pre_ping
    finally:
        engine.dispose()


def test_database_url_env_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    assert database_url() == POSTGRES_URL
    engine = make_engine()
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url() == DEFAULT_DATABASE_URL
