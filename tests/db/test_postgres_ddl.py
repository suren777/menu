"""The schema and the ref-food upsert, compiled against the PostgreSQL
dialect — no server, no Docker. Compiling the DDL is what catches the
dialect differences (JSONB, the partial-index WHERE, the ON CONFLICT
shape); the actual switch is a separate decision."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from menu.db.database import Base
from menu.nutrition.sources.common import food_upsert

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect

# .dialect() is untyped in sqlalchemy 2.1's classmethods; the instances
# are what compile() wants anyway.
SQLITE_DIALECT: Dialect = sqlite.dialect()
POSTGRES_DIALECT: Dialect = postgresql.dialect()  # type: ignore[no-untyped-call]


def _confirmed_refs_index() -> CreateIndex:
    table = Base.metadata.tables["ingredient_food_refs"]
    index = next(
        ix
        for ix in table.indexes
        if ix.name == "ix_ingredient_food_refs_confirmed_role_source"
    )
    return CreateIndex(index)


def test_confirmed_refs_index_is_partial_on_postgres() -> None:
    ddl = str(_confirmed_refs_index().compile(dialect=POSTGRES_DIALECT))
    assert "WHERE status = 'confirmed'" in ddl


def test_recipes_data_is_jsonb_on_postgres() -> None:
    ddl = str(
        CreateTable(Base.metadata.tables["recipes"]).compile(dialect=POSTGRES_DIALECT)
    )
    assert "JSONB" in ddl


def test_every_check_constraint_renders_on_postgres() -> None:
    for table in Base.metadata.sorted_tables:
        checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
        if not checks:
            continue
        ddl = str(CreateTable(table).compile(dialect=POSTGRES_DIALECT))
        for check in checks:
            name = check.name
            assert isinstance(name, str)
            assert name in ddl


@pytest.mark.parametrize(
    "dialect", [SQLITE_DIALECT, POSTGRES_DIALECT], ids=["sqlite", "postgres"]
)
def test_food_upsert_renders_on_conflict_do_update(dialect: Dialect) -> None:
    compiled = str(food_upsert(dialect.name).compile(dialect=dialect))
    assert "ON CONFLICT" in compiled
    assert "DO UPDATE" in compiled
