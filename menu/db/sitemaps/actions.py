"""Sitemap actions: recording a walk's outcome on the ledger row."""

from sqlalchemy import update

from menu.db.connection import get_session
from menu.db.database import Sitemap


def set_status(url: str, site: str, status: str, error: str | None = None) -> None:
    """Record a sitemap fetch outcome (its own transaction, so a failed
    walk still leaves the outcome in the ledger)."""
    with get_session() as session:
        session.execute(
            update(Sitemap)
            .where(Sitemap.url == url, Sitemap.site == site)
            .values(status=status, error=error)
        )
