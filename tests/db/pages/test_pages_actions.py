from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import Page
from menu.db.pages import actions

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_record_outcome_fetched(session: Session) -> None:
    """A successful attempt records the status, the HTTP status, the
    content hash and fetched_at, and bumps attempts."""
    page = Page(url="http://test.com", site="test", status="queued")
    session.add(page)
    session.flush()
    page_id = page.id

    actions.record_outcome(
        page_id,
        "fetched",
        session,
        http_status=200,
        content_sha256="abc123",
        fetched=True,
    )

    record = session.scalars(select(Page).where(Page.id == page_id)).first()
    assert record is not None
    assert record.status == "fetched"
    assert record.http_status == 200
    assert record.content_sha256 == "abc123"
    assert record.fetched_at is not None
    assert record.attempts == 1
    assert record.error is None


def test_record_outcome_bumps_attempts_per_call(session: Session) -> None:
    page = Page(url="http://test.com", site="test", status="queued")
    session.add(page)
    session.flush()
    page_id = page.id

    actions.record_outcome(page_id, "failed", session, error="FetchError: nope")
    actions.record_outcome(page_id, "fetched", session, http_status=200, fetched=True)

    record = session.scalars(select(Page).where(Page.id == page_id)).first()
    assert record is not None
    assert record.attempts == 2
    assert record.status == "fetched"


def test_record_outcome_failure_leaves_fetched_at(session: Session) -> None:
    """A failed attempt records the error but not fetched_at or a
    content hash — nothing was fetched."""
    page = Page(url="http://test.com", site="test", status="queued")
    session.add(page)
    session.flush()
    page_id = page.id

    actions.record_outcome(
        page_id, "failed", session, http_status=404, error="FetchError: Can't fetch"
    )

    record = session.scalars(select(Page).where(Page.id == page_id)).first()
    assert record is not None
    assert record.status == "failed"
    assert record.http_status == 404
    assert record.error == "FetchError: Can't fetch"
    assert record.fetched_at is None
    assert record.content_sha256 is None


def test_record_outcome_keeps_an_earlier_hash(session: Session) -> None:
    """A later blocked attempt must not wipe the hash a successful
    fetch wrote (None means leave alone)."""
    page = Page(url="http://test.com", site="test", status="fetched")
    page.content_sha256 = "abc123"
    page.fetched_at = datetime(2026, 10, 1)
    session.add(page)
    session.flush()
    page_id = page.id

    actions.record_outcome(
        page_id, "blocked", session, http_status=403, error="BlockedError"
    )

    record = session.scalars(select(Page).where(Page.id == page_id)).first()
    assert record is not None
    assert record.content_sha256 == "abc123"
    assert record.fetched_at == datetime(2026, 10, 1)
    assert record.status == "blocked"
