"""Page actions: recording the crawl ledger's outcomes."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from menu.db.connection import get_session
from menu.db.database import Page


def utcnow() -> datetime:
    """Naive UTC: the DateTime columns are `timestamp without time
    zone`, and the phase-1 bootstrap stamps cache mtimes the same way."""
    return datetime.now(UTC).replace(tzinfo=None)


def record_outcome(
    page_id: int,
    status: str,
    *,
    http_status: int | None = None,
    content_sha256: str | None = None,
    error: str | None = None,
    fetched: bool = False,
) -> None:
    """Record one fetch attempt on the page row: the status, the HTTP
    status when the response gave one, the content hash on a fetch,
    and the error.

    Its own transaction — the ledger must survive every other
    failure — and attempts is bumped server-side, so a caller cannot
    forget it.
    """
    values: dict[str, Any] = {
        "status": status,
        "attempts": Page.attempts + 1,
        "http_status": http_status,
        "error": error,
    }
    if content_sha256 is not None:
        values["content_sha256"] = content_sha256
    if fetched:
        values["fetched_at"] = utcnow()
    with get_session() as session:
        session.execute(update(Page).where(Page.id == page_id).values(**values))
