"""Rebuild tests: the whole database from the disk cache, offline.

The cache, the site registry and the network are all test-local: the
session's get raises, so any network call fails the test.
"""

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from menu.db.database import Page, Recipe, RecipeIngredient
from menu.db.engine import database_url, make_engine
from menu.db.rebuild import rebuild
from menu.ingest.fetch import _cache_path
from menu.ingest.registry import SiteConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine
    from testcontainers.community.postgres import PostgresContainer

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


def _target() -> str:
    return f"t_rebuild_{uuid.uuid4().hex}"


def _target_engine(pg_container: PostgresContainer, name: str) -> Engine:
    """An engine on a database rebuild built."""
    url = (
        make_url(pg_container.get_connection_url())
        .set(database=name)
        .render_as_string(hide_password=False)
    )
    return make_engine(url)


@pytest.mark.usefixtures("cache", "test_registry", "no_network")
def test_rebuild_replays_the_cache_offline(
    tmp_path: Path,
    pg_container: PostgresContainer,
    drop_database: Callable[[str], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One cached page is fetched with its recipe and lines; the
    uncached one stays queued; missing reference sources are skipped
    with a warning naming their import command."""
    target = _target()
    drop_database(target)  # no-op unless the rebuild leaked it
    try:
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

        engine = _target_engine(pg_container, target)
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
    finally:
        drop_database(target)


def test_rebuild_refuses_the_live_database_name() -> None:
    """A rebuild must never target the database the live URL names."""
    live = make_url(database_url()).database
    assert live is not None
    with pytest.raises(ValueError, match="live database"):
        rebuild(live)


def test_rebuild_rejects_an_invalid_target_name() -> None:
    """Target names are quoted into DDL, so the shape is validated."""
    with pytest.raises(ValueError, match="invalid target database name"):
        rebuild("Not-Valid")


def test_rebuild_refuses_an_existing_target(
    make_database: Callable[[], Engine],
) -> None:
    """The refusal happens before any cache or engine work."""
    target = make_database().url.database
    assert target is not None
    with pytest.raises(FileExistsError, match="already exists"):
        rebuild(target)


@pytest.mark.usefixtures("cache", "test_registry", "no_network")
def test_rebuild_force_builds_into_its_target_not_the_default(
    tmp_path: Path,
    db_engine: Engine,
    make_database: Callable[[], Engine],
) -> None:
    """--force drops an existing target and builds into it: the
    rebuild's engine override scopes its writes to the target — the
    default engine (patched here, as the conftest does) sees none of
    them."""
    target = make_database().url.database
    assert target is not None
    rebuild(target, sources_dir=tmp_path / "sources", force=True)

    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(Page)) == 0
