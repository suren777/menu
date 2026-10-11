from datetime import datetime

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import Page
from menu.db.pages import actions


@pytest.mark.usefixtures("db_engine")
def test_record_outcome_fetched() -> None:
    """A successful attempt records the status, the HTTP status, the
    content hash and fetched_at, and bumps attempts. Its own
    transaction, so the seed must be committed before it runs."""
    with get_session() as session:
        page = Page(url="http://test.com", site="test", status="queued")
        session.add(page)
        session.flush()
        page_id = page.id

    actions.record_outcome(
        page_id,
        "fetched",
        http_status=200,
        content_sha256="abc123",
        fetched=True,
    )

    with get_session() as session:
        record = session.scalars(select(Page).where(Page.id == page_id)).first()
        assert record is not None
        assert record.status == "fetched"
        assert record.http_status == 200
        assert record.content_sha256 == "abc123"
        assert record.fetched_at is not None
        assert record.attempts == 1
        assert record.error is None


@pytest.mark.usefixtures("db_engine")
def test_record_outcome_bumps_attempts_per_call() -> None:
    with get_session() as session:
        page = Page(url="http://test.com", site="test", status="queued")
        session.add(page)
        session.flush()
        page_id = page.id

    actions.record_outcome(page_id, "failed", error="FetchError: nope")
    actions.record_outcome(page_id, "fetched", http_status=200, fetched=True)

    with get_session() as session:
        record = session.scalars(select(Page).where(Page.id == page_id)).first()
        assert record is not None
        assert record.attempts == 2
        assert record.status == "fetched"


@pytest.mark.usefixtures("db_engine")
def test_record_outcome_failure_leaves_fetched_at() -> None:
    """A failed attempt records the error but not fetched_at or a
    content hash — nothing was fetched."""
    with get_session() as session:
        page = Page(url="http://test.com", site="test", status="queued")
        session.add(page)
        session.flush()
        page_id = page.id

    actions.record_outcome(
        page_id, "failed", http_status=404, error="FetchError: Can't fetch"
    )

    with get_session() as session:
        record = session.scalars(select(Page).where(Page.id == page_id)).first()
        assert record is not None
        assert record.status == "failed"
        assert record.http_status == 404
        assert record.error == "FetchError: Can't fetch"
        assert record.fetched_at is None
        assert record.content_sha256 is None


@pytest.mark.usefixtures("db_engine")
def test_record_outcome_keeps_an_earlier_hash() -> None:
    """A later blocked attempt must not wipe the hash a successful
    fetch wrote (None means leave alone)."""
    with get_session() as session:
        page = Page(url="http://test.com", site="test", status="fetched")
        page.content_sha256 = "abc123"
        page.fetched_at = datetime(2026, 10, 1)
        session.add(page)
        session.flush()
        page_id = page.id

    actions.record_outcome(page_id, "blocked", http_status=403, error="BlockedError")

    with get_session() as session:
        record = session.scalars(select(Page).where(Page.id == page_id)).first()
        assert record is not None
        assert record.content_sha256 == "abc123"
        assert record.fetched_at == datetime(2026, 10, 1)
        assert record.status == "blocked"
