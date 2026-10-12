"""Page actions: recording the crawl ledger's outcomes."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import update

from menu.db.database import Page

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def utcnow() -> datetime:
    """Naive UTC: the DateTime columns are `timestamp without time
    zone`, and the phase-1 bootstrap stamps cache mtimes the same way."""
    return datetime.now(UTC).replace(tzinfo=None)


def record_outcome(
    page_id: int,
    status: str,
    session: Session,
    *,
    http_status: int | None = None,
    content_sha256: str | None = None,
    error: str | None = None,
    fetched: bool = False,
) -> None:
    """Record one fetch attempt on the page row: the status, the HTTP
    status when the response gave one, the content hash on a fetch,
    and the error.

    The caller owns the transaction — the pipeline gives each ledger
    write its own, so it survives every later failure — and attempts is
    bumped server-side, so a caller cannot forget it.
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
    session.execute(update(Page).where(Page.id == page_id).values(**values))
