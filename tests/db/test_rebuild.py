"""Rebuild tests: the whole database from the disk cache, offline.

The cache, the site registry and the network are all test-local: the
session's get raises, so any network call fails the test.
"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from menu.db.database import Page, Recipe, RecipeIngredient
from menu.db.rebuild import rebuild
from menu.ingest.fetch import _cache_path
from menu.ingest.registry import SiteConfig

SITE = SiteConfig(
    name="testsite",
    base_url="https://test.com",
    sitemap_urls=("https://test.com/sitemap.xml",),
)

SITEMAP_URL = "https://test.com/sitemap.xml"
# The dip page's HTML is cached; the cake page is not.
DIP_URL = "https://test.com/recipes/dip"
CAKE_URL = "https://test.com/recipes/cake"
SITEMAP_XML = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<url><loc>{DIP_URL}</loc></url>"
    f"<url><loc>{CAKE_URL}</loc></url>"
    "</urlset>".encode()
)
# A saved page: JSON-LD Recipe with twelve ingredient lines.
SAVED_PAGE = (
    Path(__file__).resolve().parent.parent
    / "ingest"
    / "fixtures"
    / "ottolenghi_control_chars.html"
).read_bytes()


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A private cache dir holding the sitemap and one page's HTML,
    with fetch.py pointed at it."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr("menu.ingest.fetch.DEFAULT_CACHE_DIR", cache_dir)
    _cache_path(SITEMAP_URL, cache_dir).write_bytes(SITEMAP_XML)
    _cache_path(DIP_URL, cache_dir).write_bytes(SAVED_PAGE)
    return cache_dir


@pytest.fixture
def test_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the test site is registered."""
    monkeypatch.setattr("menu.ingest.sites.registry", lambda: {"testsite": SITE})


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any network call fails the test."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("network call in an offline test")

    monkeypatch.setattr("menu.ingest.http.session.get", boom)


@pytest.mark.usefixtures("cache", "test_registry", "no_network")
def test_rebuild_replays_the_cache_offline(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One cached page is fetched with its recipe and lines; the
    uncached one stays queued; missing reference sources are skipped
    with a warning naming their import command."""
    target = tmp_path / "rebuilt.db"
    with caplog.at_level(logging.WARNING, logger="menu.db.rebuild"):
        summary = rebuild(target, sources_dir=tmp_path / "sources")

    assert summary["sources"] == {
        "imported": [],
        "skipped": ["fdc", "cofid", "cnf"],
    }
    assert summary["sites"] == {
        "testsite": {"pages": {"fetched": 1, "queued": 1}, "recipes": 1}
    }
    for source in ("fdc", "cofid", "cnf"):
        assert f"menu-{source}-import" in caplog.text

    engine = create_engine(f"sqlite:///{target}")
    try:
        with Session(engine) as session:
            pages = {page.url: page for page in session.scalars(select(Page)).all()}
            assert pages[DIP_URL].status == "fetched"
            assert pages[CAKE_URL].status == "queued"
            assert pages[CAKE_URL].attempts == 0
            recipe = session.scalars(select(Recipe)).one()
            assert recipe.page_id == pages[DIP_URL].id
            assert recipe.name == "Pea and artichoke dip with pickled onions"
            lines = session.scalars(select(RecipeIngredient)).all()
            assert len(lines) >= 1
    finally:
        engine.dispose()


def test_rebuild_refuses_an_existing_target(tmp_path: Path) -> None:
    """The refusal happens before any cache or engine work."""
    target = tmp_path / "rebuilt.db"
    target.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="already exists"):
        rebuild(target, sources_dir=tmp_path / "sources")
