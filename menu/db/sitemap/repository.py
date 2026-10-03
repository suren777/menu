from dataclasses import dataclass

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from menu.db.database import Sitemap


@dataclass
class SitemapModel:
    id: int
    url: str
    site: str
    completed: bool


class SitemapRepository:
    @staticmethod
    def from_record(record: Sitemap) -> SitemapModel:
        return SitemapModel(
            id=int(record.id),
            url=str(record.url),
            site=str(record.site),
            completed=bool(record.completed),
        )

    @staticmethod
    def to_record(entity: SitemapModel) -> Sitemap:
        return Sitemap(
            id=entity.id, url=entity.url, site=entity.site, completed=entity.completed
        )

    def find_by_url(self, url: str, session: Session) -> SitemapModel | None:
        result = session.query(Sitemap).filter(Sitemap.url == url).first()
        if result is not None:
            return SitemapRepository.from_record(result)
        return None

    @staticmethod
    def url_exists(url: str, site: str, session: Session) -> bool:
        return bool(
            session.query(
                select(Sitemap)
                .filter(Sitemap.url == url, Sitemap.site == site)
                .exists()
            ).scalar()
        )

    @staticmethod
    def get_all(session: Session) -> list[SitemapModel]:
        return [
            SitemapRepository.from_record(record) for record in session.query(Sitemap)
        ]

    @staticmethod
    def get_unfinished(session: Session, site: str) -> list[SitemapModel]:
        return [
            SitemapRepository.from_record(record)
            for record in session.query(Sitemap).filter(
                Sitemap.completed == false(), Sitemap.site == site
            )
        ]
