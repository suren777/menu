from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from menu.db.database import RecipeUrls


@dataclass
class RecipeUrlsModel:
    id: int
    url: str
    name: str
    data: dict[str, Any]


class RecipeUrlsRepository:
    @staticmethod
    def from_record(record: RecipeUrls) -> RecipeUrlsModel:
        return RecipeUrlsModel(
            id=int(record.id),
            url=str(record.url),
            name=str(record.name),
            data=dict(record.data),
        )

    @staticmethod
    def to_record(entity: RecipeUrlsModel) -> RecipeUrls:
        return RecipeUrls(
            id=entity.id, url=entity.url, name=entity.name, data=entity.data
        )

    def find_by_url(self, url: str, session: Session) -> RecipeUrlsModel | None:
        result = session.scalars(select(RecipeUrls).filter_by(url=url)).first()
        if result is not None:
            return RecipeUrlsRepository.from_record(result)
        return None

    @staticmethod
    def url_exists(url: str, session: Session) -> bool:
        return session.query(
            select(RecipeUrls).filter(RecipeUrls.url == url).exists()
        ).scalar()

    @staticmethod
    def get_all(session: Session) -> list[RecipeUrlsModel]:
        return [
            RecipeUrlsRepository.from_record(record)
            for record in session.query(RecipeUrls)
        ]

    @staticmethod
    def get_all_by_id(ids: list[int], session: Session) -> list[RecipeUrlsModel]:
        return [
            RecipeUrlsRepository.from_record(record)
            for record in session.query(RecipeUrls).filter(RecipeUrls.id.in_(ids))
        ]

    @staticmethod
    def add_recipe(url: str, name: str, recipe_data: dict[str, Any], session: Session):
        if not RecipeUrlsRepository.url_exists(url, session):
            session.add(RecipeUrls(url=url, name=name, data=recipe_data))
