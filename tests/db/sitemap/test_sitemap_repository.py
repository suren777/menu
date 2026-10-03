from typing import TYPE_CHECKING

from menu.db.database import Sitemap
from menu.db.sitemap import repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_to_model() -> None:
    record = Sitemap(id=1, url="http://test.com", site="test", completed=False)

    model = repository.to_model(record)

    assert model.id == 1
    assert model.url == "http://test.com"
    assert model.site == "test"
    assert not model.completed


def test_find_by_url(session: Session) -> None:
    session.add(Sitemap(url="http://test.com", site="test"))
    session.flush()

    model = repository.find_by_url("http://test.com", session)

    assert model is not None
    assert model.site == "test"
    assert not model.completed


def test_find_by_url_missing(session: Session) -> None:
    assert repository.find_by_url("http://missing.com", session) is None


def test_url_exists_is_scoped_by_site(session: Session) -> None:
    session.add(Sitemap(url="http://test.com", site="bbc_good_food"))
    session.flush()

    assert repository.url_exists("http://test.com", "bbc_good_food", session)
    assert not repository.url_exists("http://test.com", "other", session)
    assert not repository.url_exists("http://missing.com", "bbc_good_food", session)


def test_get_all(session: Session) -> None:
    session.add(Sitemap(url="http://test.com", site="test"))
    session.add(Sitemap(url="http://other.com", site="test"))
    session.flush()

    models = repository.get_all(session)

    assert {model.url for model in models} == {"http://test.com", "http://other.com"}


def test_get_unfinished_filters_completed_and_site(session: Session) -> None:
    session.add(Sitemap(url="http://finished.com", site="test", completed=True))
    session.add(Sitemap(url="http://other-site.com", site="other"))
    session.add(Sitemap(url="http://test.com", site="test"))
    session.flush()

    urls = [model.url for model in repository.get_unfinished(session, "test")]

    assert urls == ["http://test.com"]
