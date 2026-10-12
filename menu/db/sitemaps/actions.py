"""Sitemap actions: recording a walk's outcome on the ledger row."""

from typing import TYPE_CHECKING

from sqlalchemy import update

from menu.db.database import Sitemap

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def set_status(
    url: str, site: str, status: str, session: Session, error: str | None = None
) -> None:
    """Record a sitemap fetch outcome (the caller owns the transaction:
    the crawl gives each ledger write its own, so a failed walk still
    leaves the outcome in the ledger)."""
    session.execute(
        update(Sitemap)
        .where(Sitemap.url == url, Sitemap.site == site)
        .values(status=status, error=error)
    )
