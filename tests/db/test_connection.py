"""Connection tests: the use_engine override and its resolution order."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine

from menu.db.connection import get_session, use_engine

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


def test_use_engine_binds_get_session(db_engine: Engine, tmp_path: Path) -> None:
    """Inside use_engine(e), get_session() binds to e without being
    told; after the block it is back to the default."""
    override = create_engine(f"sqlite:///{tmp_path}/override.db")
    with use_engine(override), get_session() as session:
        assert session.get_bind() is override
    with get_session() as session:
        assert session.get_bind() is db_engine


def test_use_engine_resets_after_an_exception(
    db_engine: Engine, tmp_path: Path
) -> None:
    """The override does not leak when the body raises."""
    override = create_engine(f"sqlite:///{tmp_path}/override.db")
    with pytest.raises(RuntimeError, match="boom"), use_engine(override):
        raise RuntimeError("boom")
    with get_session() as session:
        assert session.get_bind() is db_engine


def test_explicit_engine_beats_the_override(db_engine: Engine, tmp_path: Path) -> None:
    """An explicit argument wins over use_engine."""
    explicit = create_engine(f"sqlite:///{tmp_path}/explicit.db")
    with use_engine(explicit), get_session(db_engine) as session:
        assert session.get_bind() is db_engine
