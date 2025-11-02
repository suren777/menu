from unittest.mock import MagicMock
from menu.db.sitemap.repository import SitemapRepository
from menu.db.database import Sitemap

def test_from_record():
    record = Sitemap(id=1, url="http://test.com", completed=False)
    model = SitemapRepository.from_record(record)
    assert model.id == 1
    assert model.url == "http://test.com"
    assert not model.completed

def test_to_record():
    model = MagicMock()
    model.id = 1
    model.url = "http://test.com"
    model.completed = False
    record = SitemapRepository.to_record(model)
    assert record.id == 1
    assert record.url == "http://test.com"
    assert not record.completed

def test_find_by_url():
    session = MagicMock()
    repo = SitemapRepository()
    repo.find_by_url("http://test.com", session)
    session.query.assert_called_once()

def test_url_exists():
    session = MagicMock()
    SitemapRepository.url_exists("http://test.com", session)
    session.query.assert_called_once()

def test_get_all():
    session = MagicMock()
    SitemapRepository.get_all(session)
    session.query.assert_called_once()

def test_get_unfinished():
    session = MagicMock()
    SitemapRepository.get_unfinished(session)
    session.query.assert_called_once()
