from dataclasses import dataclass
from sqlalchemy import select, false
from menu.db.database import Sitemap
from sqlalchemy.orm import Session


@dataclass
class SitemapModel:
    id: int
    url: str
    completed: bool


class SitemapRepository:
    @staticmethod
    def from_record(record: Sitemap) -> SitemapModel:
        return SitemapModel(id=record.id, url=record.url, completed=record.completed)

    @staticmethod
    def to_record(entity: SitemapModel) -> Sitemap:
        return Sitemap(id=entity.id, url=entity.url, completed=entity.completed)

    def find_by_url(self, url: str, session: Session) -> SitemapModel | None:
        result = session.query(select(SitemapModel).filter(SitemapModel.url == url))
        if result is not None:
            return SitemapRepository.from_record(result)
        return None

    @staticmethod
    def url_exists(url: str, session: Session) -> bool:
        return session.query(
            select(Sitemap).filter(Sitemap.url == url).exists()
        ).scalar()

    def get_all(session: Session) -> list[SitemapModel]:
        return [
            SitemapRepository.from_record(record) for record in session.query(Sitemap)
        ]

    @staticmethod
    def get_unfinished(session: Session) -> list[SitemapModel]:
        return [
            SitemapRepository.from_record(record)
            for record in session.query(Sitemap).filter(Sitemap.completed == false())
        ]
