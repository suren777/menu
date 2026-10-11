"""Sitemap queries: plain functions over a caller-supplied session."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import Sitemap

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


@dataclass
class SitemapModel:
    id: int
    url: str
    site: str
    parent_id: int | None
    kind: str | None
    status: str
    fetched_at: datetime | None
    error: str | None


def to_model(record: Sitemap) -> SitemapModel:
    return SitemapModel(
        id=record.id,
        url=record.url,
        site=record.site,
        parent_id=record.parent_id,
        kind=record.kind,
        status=record.status,
        fetched_at=record.fetched_at,
        error=record.error,
    )


def find_by_url(url: str, session: Session) -> SitemapModel | None:
    record = session.scalars(select(Sitemap).where(Sitemap.url == url)).first()
    return to_model(record) if record is not None else None


def get_all(session: Session) -> list[SitemapModel]:
    return [to_model(record) for record in session.scalars(select(Sitemap))]


def get_unfinished(session: Session, site: str) -> list[SitemapModel]:
    """The site's sitemaps still to walk: never fetched (pending), or
    blocked by the last attempt — a block is retried, a failure needs
    a human or a fix before it is worth retrying."""
    return [
        to_model(record)
        for record in session.scalars(
            select(Sitemap)
            .where(Sitemap.site == site, Sitemap.status.in_(("pending", "blocked")))
            .order_by(Sitemap.id)
        )
    ]
