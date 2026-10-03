"""RecipeUrls queries: plain functions over a caller-supplied session."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from menu.db.database import RecipeUrls

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class RecipeUrlsModel:
    id: int
    url: str
    name: str
    data: dict[str, Any]


def to_model(record: RecipeUrls) -> RecipeUrlsModel:
    return RecipeUrlsModel(
        id=record.id, url=record.url, name=record.name, data=dict(record.data)
    )


def find_by_url(url: str, session: Session) -> RecipeUrlsModel | None:
    record = session.scalars(select(RecipeUrls).where(RecipeUrls.url == url)).first()
    return to_model(record) if record is not None else None


def get_all(session: Session) -> list[RecipeUrlsModel]:
    return [to_model(record) for record in session.scalars(select(RecipeUrls))]


def get_all_by_id(ids: list[int], session: Session) -> list[RecipeUrlsModel]:
    return [
        to_model(record)
        for record in session.scalars(select(RecipeUrls).where(RecipeUrls.id.in_(ids)))
    ]


def add_recipe(
    url: str, name: str, recipe_data: dict[str, Any], session: Session
) -> None:
    """Insert the recipe; a URL that is already stored is left untouched.

    The conflict is ignored in the INSERT itself (RecipeUrls.url is
    unique), so no lookup is needed first.
    """
    session.execute(
        insert(RecipeUrls)
        .values(url=url, name=name, data=recipe_data)
        .on_conflict_do_nothing(index_elements=["url"])
    )
