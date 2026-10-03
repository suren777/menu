"""Sitemap queries: plain functions over a caller-supplied session."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import exists, false, select

from menu.db.database import Sitemap

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class SitemapModel:
    id: int
    url: str
    site: str
    completed: bool


def to_model(record: Sitemap) -> SitemapModel:
    return SitemapModel(
        id=record.id, url=record.url, site=record.site, completed=record.completed
    )


def find_by_url(url: str, session: Session) -> SitemapModel | None:
    record = session.scalars(select(Sitemap).where(Sitemap.url == url)).first()
    return to_model(record) if record is not None else None


def url_exists(url: str, site: str, session: Session) -> bool:
    return bool(
        session.scalar(select(exists().where(Sitemap.url == url, Sitemap.site == site)))
    )


def get_all(session: Session) -> list[SitemapModel]:
    return [to_model(record) for record in session.scalars(select(Sitemap))]


def get_unfinished(session: Session, site: str) -> list[SitemapModel]:
    return [
        to_model(record)
        for record in session.scalars(
            select(Sitemap).where(Sitemap.completed == false(), Sitemap.site == site)
        )
    ]
