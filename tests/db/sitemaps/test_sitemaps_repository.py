from typing import TYPE_CHECKING

from menu.db.database import Sitemap
from menu.db.sitemaps import repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_to_model() -> None:
    record = Sitemap(
        id=1, url="http://test.com", site="test", status="pending", kind="urlset"
    )

    model = repository.to_model(record)

    assert model.id == 1
    assert model.url == "http://test.com"
    assert model.site == "test"
    assert model.status == "pending"
    assert model.kind == "urlset"


def test_find_by_url(session: Session) -> None:
    session.add(Sitemap(url="http://test.com", site="test", status="pending"))
    session.flush()

    model = repository.find_by_url("http://test.com", session)

    assert model is not None
    assert model.site == "test"
    assert model.status == "pending"


def test_find_by_url_missing(session: Session) -> None:
    assert repository.find_by_url("http://missing.com", session) is None


def test_get_all(session: Session) -> None:
    session.add(Sitemap(url="http://test.com", site="test", status="pending"))
    session.add(Sitemap(url="http://other.com", site="test", status="pending"))
    session.flush()

    models = repository.get_all(session)

    assert {model.url for model in models} == {"http://test.com", "http://other.com"}


def test_get_unfinished_takes_pending_and_blocked(session: Session) -> None:
    """A blocked sitemap is retried on the next run: it is unfinished.
    A failed one is not (it needs eyes, not a retry), and neither are
    other sites' sitemaps."""
    session.add(Sitemap(url="http://done.com", site="test", status="done"))
    session.add(Sitemap(url="http://failed.com", site="test", status="failed"))
    session.add(Sitemap(url="http://other.com", site="other", status="pending"))
    session.add(Sitemap(url="http://blocked.com", site="test", status="blocked"))
    session.add(Sitemap(url="http://test.com", site="test", status="pending"))
    session.flush()

    models = repository.get_unfinished(session, "test")

    assert [model.url for model in models] == ["http://blocked.com", "http://test.com"]
