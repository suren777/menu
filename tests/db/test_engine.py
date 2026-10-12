"""The engine: DATABASE_URL wins over the default, make_engine builds
a pooled engine for whatever URL it is given, and the default engine
is a cached factory that importing the module does not build."""

import subprocess
import sys
from typing import TYPE_CHECKING

from menu.db.engine import (
    DEFAULT_DATABASE_URL,
    database_url,
    default_engine,
    make_engine,
)

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


def test_default_engine_is_cached() -> None:
    """default_engine() builds the engine on first use and hands out
    the same one afterwards."""
    assert default_engine() is default_engine()


def test_importing_the_module_builds_no_engine() -> None:
    """Importing menu.db.engine has no side effect: no engine exists
    until default_engine() is first called."""
    script = (
        "import menu.db.engine as m; assert m.default_engine.cache_info().currsize == 0"
    )
    subprocess.run(  # noqa: S603 - a fixed literal script, nothing untrusted
        [sys.executable, "-c", script], check=True
    )


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
