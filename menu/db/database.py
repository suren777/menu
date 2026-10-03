"""Models for the scratch SQLite database.

Typed 2.0-style columns (Mapped/mapped_column) so mypy sees the column
types and call sites need no casts.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from menu.db.engine import engine as default_engine

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class Base(DeclarativeBase):
    pass


class Sitemap(Base):
    __tablename__ = "sitemap"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(nullable=False, unique=True)
    site: Mapped[str] = mapped_column(nullable=False)
    """Registry name of the site this sitemap belongs to."""
    completed: Mapped[bool] = mapped_column(default=False)


class RecipeUrls(Base):
    __tablename__ = "recipe_urls"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(nullable=False, unique=True)
    """Unique: recipes are deduplicated on their URL."""
    name: Mapped[str] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


def initialise(db_engine: Engine | None = None) -> None:
    """Create the tables. The default engine is resolved at call time so
    tests can patch it."""
    Base.metadata.create_all(db_engine or default_engine)
