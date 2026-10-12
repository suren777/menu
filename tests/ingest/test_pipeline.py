"""Pipeline tests: the crawl ledger and the atomic recipe write."""

import hashlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    Sitemap,
)
from menu.db.ingredients import suggestions
from menu.db.pages import repository as pages_repository
from menu.db.sitemaps import actions as sitemap_actions
from menu.ingest import pipeline
from menu.ingest.discover import SitemapEntry
from menu.ingest.errors import (
    BlockedError,
    DisallowedError,
    FetchError,
)
from menu.ingest.http import http_session
from menu.ingest.llm_review import Usage, prompt_hash
from menu.ingest.pipeline import (
    CrawlReport,
    crawl_sitemap,
    import_sitemap,
    main,
    process_url,
    reparse_site,
)
from menu.ingest.registry import SiteConfig, get_site
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from menu.db.pages.repository import PageModel

SITE = SiteConfig(
    name="test",
    base_url="https://test.com",
    sitemap_urls=("https://test.com/sitemap.xml",),
)

RECIPE_DATA: dict[str, Any] = {"@type": "Recipe", "name": "Cake"}
PAGE_CONTENT = b"<html><head><title>Page title</title></head><body></body></html>"


def add_page(url: str, status: str = "queued", site: str = "test") -> int:
    with get_session() as session:
        page = Page(url=url, site=site, status=status)
        session.add(page)
        session.flush()
        return page.id


def add_sitemap(url: str, status: str = "pending", site: str = "test") -> int:
    with get_session() as session:
        sitemap = Sitemap(url=url, site=site, status=status)
        session.add(sitemap)
        session.flush()
        return sitemap.id


def get_page_record(session: Session, url: str) -> Page:
    record = session.scalars(select(Page).where(Page.url == url)).first()
    assert record is not None
    return record


def get_page_model(url: str) -> PageModel:
    with get_session() as session:
        model = pages_repository.find_by_url(url, session)
    assert model is not None
    return model


# --- import_sitemap: the sitemap ledger -----------------------------------


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.request_xml")
def test_import_sitemap_index_stores_children_as_pending(
    mock_request_xml: MagicMock,
) -> None:
    mock_request_xml.return_value = (
        "sitemapindex",
        [SitemapEntry(loc="https://test.com/child.xml")],
    )

    import_sitemap(SITE)

    with get_session() as session:
        parent = session.scalars(
            select(Sitemap).where(Sitemap.url == "https://test.com/sitemap.xml")
        ).first()
        child = session.scalars(
            select(Sitemap).where(Sitemap.url == "https://test.com/child.xml")
        ).first()
        assert parent is not None
        assert child is not None
        assert parent.kind == "index"
        assert parent.status == "done"
        assert parent.fetched_at is not None
        assert child.status == "pending"
        assert child.parent_id == parent.id


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.request_xml")
def test_import_sitemap_urlset_stores_itself_pending(
    mock_request_xml: MagicMock,
) -> None:
    mock_request_xml.return_value = (
        "urlset",
        [SitemapEntry(loc="https://test.com/a")],
    )

    import_sitemap(SITE)

    with get_session() as session:
        rows = session.scalars(select(Sitemap)).all()
        assert len(rows) == 1
        assert rows[0].url == "https://test.com/sitemap.xml"
        assert rows[0].kind == "urlset"
        assert rows[0].status == "pending"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.request_xml")
def test_import_sitemap_rerun_adds_new_children(mock_request_xml: MagicMock) -> None:
    """A re-run re-reads the index: new children are queued, existing
    rows keep their recorded outcome."""
    mock_request_xml.return_value = (
        "sitemapindex",
        [SitemapEntry(loc="https://test.com/old.xml")],
    )
    import_sitemap(SITE)
    sitemap_actions.set_status("https://test.com/old.xml", SITE.name, "done")
    mock_request_xml.return_value = (
        "sitemapindex",
        [
            SitemapEntry(loc="https://test.com/old.xml"),
            SitemapEntry(loc="https://test.com/new.xml"),
        ],
    )

    import_sitemap(SITE)

    with get_session() as session:
        status = {row.url: row.status for row in session.scalars(select(Sitemap)).all()}
    assert status == {
        "https://test.com/sitemap.xml": "done",
        "https://test.com/old.xml": "done",
        "https://test.com/new.xml": "pending",
    }


# --- crawl_sitemap: walking sitemaps queues pages --------------------------


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_queues_pages_from_pending_sitemaps(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    sitemap_id = add_sitemap("https://test.com/sitemap1.xml")
    mock_request_xml.return_value = (
        "urlset",
        [
            SitemapEntry(loc="https://test.com/recipes/a", lastmod="2026-10-01"),
            SitemapEntry(loc="https://test.com/about"),
        ],
    )
    mock_process_url.return_value = "fetched"

    report = crawl_sitemap(SITE)

    with get_session() as session:
        pages = {page.url: page for page in session.scalars(select(Page)).all()}
        assert set(pages) == {
            "https://test.com/recipes/a",
            "https://test.com/about",
        }
        queued = pages["https://test.com/recipes/a"]
        assert queued.status == "queued"
        assert queued.site == "test"
        assert queued.lastmod == "2026-10-01"
        assert queued.sitemap_id == sitemap_id
        sitemap = session.scalars(
            select(Sitemap).where(Sitemap.id == sitemap_id)
        ).first()
        assert sitemap is not None
        assert sitemap.status == "done"
        assert sitemap.kind == "urlset"
        assert sitemap.fetched_at is not None
    assert report == CrawlReport(fetched=2, stored=2)


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_queues_only_pattern_matching_pages(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    add_sitemap("https://test.com/sitemap1.xml")
    mock_request_xml.return_value = (
        "urlset",
        [
            SitemapEntry(loc="https://test.com/recipes/cake"),
            SitemapEntry(loc="https://test.com/recipes/cake/step-2"),
            SitemapEntry(loc="https://test.com/about"),
        ],
    )
    mock_process_url.return_value = "fetched"
    site = SITE.model_copy(update={"url_pattern": r"^https://test\.com/recipes/[^/]+$"})

    crawl_sitemap(site)

    with get_session() as session:
        assert [page.url for page in session.scalars(select(Page)).all()] == [
            "https://test.com/recipes/cake"
        ]


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_skips_pages_already_in_the_ledger(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    """A URL already in pages is never queued twice: the existing row
    (with its recorded outcome) is left alone."""
    add_page("https://test.com/recipes/a", status="fetched")
    add_sitemap("https://test.com/sitemap1.xml")
    mock_request_xml.return_value = (
        "urlset",
        [SitemapEntry(loc="https://test.com/recipes/a", lastmod="2026-10-01")],
    )
    mock_process_url.return_value = "fetched"

    crawl_sitemap(SITE)

    with get_session() as session:
        rows = session.scalars(select(Page)).all()
        assert len(rows) == 1
        assert rows[0].status == "fetched"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_does_not_rewalk_done_sitemaps(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    """A re-run continues from the queued pages: done sitemaps are not
    fetched again."""
    add_sitemap("https://test.com/sitemap1.xml", status="done")
    add_page("https://test.com/recipes/a")
    mock_process_url.return_value = "fetched"

    report = crawl_sitemap(SITE)

    mock_request_xml.assert_not_called()
    assert report == CrawlReport(fetched=1, stored=1)


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_rewalks_blocked_sitemaps(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    """A blocked sitemap is unfinished work: the next run retries it."""
    add_sitemap("https://test.com/sitemap1.xml", status="blocked")
    mock_request_xml.return_value = ("urlset", [])

    crawl_sitemap(SITE)

    mock_process_url.assert_not_called()
    with get_session() as session:
        sitemap = session.scalars(select(Sitemap)).first()
        assert sitemap is not None
        assert sitemap.status == "done"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_marks_failed_sitemap_and_moves_on(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    dead = add_sitemap("https://test.com/dead.xml")
    live = add_sitemap("https://test.com/live.xml")
    mock_request_xml.side_effect = [ValueError("junk"), ("urlset", [])]

    report = crawl_sitemap(SITE)

    mock_process_url.assert_not_called()

    with get_session() as session:
        failed = session.scalars(select(Sitemap).where(Sitemap.id == dead)).first()
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error == "ValueError: junk"
        walked = session.scalars(select(Sitemap).where(Sitemap.id == live)).first()
        assert walked is not None
        assert walked.status == "done"
    assert report.failed == 1


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.request_xml")
def test_crawl_sitemap_blocked_sitemap_stops_the_run(
    mock_request_xml: MagicMock,
    mock_process_url: MagicMock,
) -> None:
    """A site-wide block stops everything: the sitemap is marked
    blocked and no pages are fetched."""
    sitemap_id = add_sitemap("https://test.com/sitemap1.xml")
    add_page("https://test.com/recipes/a")
    mock_request_xml.side_effect = BlockedError(
        "Blocked with HTTP 402", status_code=402
    )

    report = crawl_sitemap(SITE)

    with get_session() as session:
        sitemap = session.scalars(
            select(Sitemap).where(Sitemap.id == sitemap_id)
        ).first()
        assert sitemap is not None
        assert sitemap.status == "blocked"
        assert sitemap.error == "BlockedError: Blocked with HTTP 402"
    mock_process_url.assert_not_called()
    assert report == CrawlReport()


# --- crawl_sitemap: the page queue -----------------------------------------


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
def test_crawl_sitemap_processes_only_queued_and_blocked_pages(
    mock_process_url: MagicMock,
) -> None:
    """A re-run processes queued pages (and retries blocked ones);
    everything already resolved is left alone."""
    add_page("https://test.com/queued", status="queued")
    add_page("https://test.com/blocked", status="blocked")
    add_page("https://test.com/fetched", status="fetched")
    add_page("https://test.com/norecipe", status="no_recipe")
    processed: list[str] = []

    def fake_process_url(page: Any, _site: SiteConfig, offline: bool) -> str:
        assert not offline  # a live crawl never passes offline
        processed.append(page.url)
        return "fetched"

    mock_process_url.side_effect = fake_process_url

    crawl_sitemap(SITE)

    assert processed == ["https://test.com/queued", "https://test.com/blocked"]


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
def test_crawl_sitemap_counts_the_statuses(
    mock_process_url: MagicMock,
) -> None:
    for index in range(4):
        add_page(f"https://test.com/recipes/{index}")
    mock_process_url.side_effect = ["fetched", "no_recipe", "failed", "fetched"]

    report = crawl_sitemap(SITE)

    assert report == CrawlReport(fetched=4, stored=2, failed=1)


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
def test_crawl_sitemap_stops_after_five_consecutive_blocks(
    mock_process_url: MagicMock,
) -> None:
    for index in range(6):
        add_page(f"https://test.com/recipes/{index}")
    mock_process_url.side_effect = BlockedError("Blocked with HTTP 403")

    crawl_sitemap(SITE)

    assert mock_process_url.call_count == 5


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
def test_crawl_sitemap_resets_block_streak(
    mock_process_url: MagicMock,
) -> None:
    for index in range(3):
        add_page(f"https://test.com/recipes/{index}")
    mock_process_url.side_effect = [
        BlockedError("Blocked with HTTP 403"),
        "fetched",
        BlockedError("Blocked with HTTP 403"),
    ]

    report = crawl_sitemap(SITE)

    assert mock_process_url.call_count == 3
    assert report == CrawlReport(fetched=3, stored=1)


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.process_url")
def test_crawl_sitemap_prints_the_report(
    mock_process_url: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_page("https://test.com/recipes/a")
    mock_process_url.return_value = "fetched"

    crawl_sitemap(SITE)

    assert "Done: 1 fetched, 1 stored, 0 failed" in capsys.readouterr().out


# --- process_url: recording every outcome ----------------------------------


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.store_recipe_ingredients")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_process_url_records_a_fetched_page(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
    mock_store: MagicMock,
) -> None:
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = (RECIPE_DATA, "json_ld")

    status = process_url(get_page_model("https://test.com/recipes/cake"), SITE)

    assert status == "fetched"
    mock_fetch.assert_called_once_with(
        "https://test.com/recipes/cake", SITE, offline=False
    )
    mock_store.assert_called_once()
    with get_session() as session:
        record = get_page_record(session, "https://test.com/recipes/cake")
        assert record.status == "fetched"
        assert record.http_status == 200
        assert record.attempts == 1
        assert record.content_sha256 == hashlib.sha256(PAGE_CONTENT).hexdigest()
        assert record.fetched_at is not None
        assert record.error is None
        recipe = session.scalars(select(Recipe)).first()
        assert recipe is not None
        assert recipe.page_id == record.id
        assert recipe.name == "Cake"
        assert recipe.extractor == "json_ld"
        assert recipe.data_sha256 == pages_repository.data_sha256(RECIPE_DATA)
        assert recipe.data == RECIPE_DATA


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_process_url_records_no_recipe(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
) -> None:
    add_page("https://test.com/about")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = None

    status = process_url(get_page_model("https://test.com/about"), SITE)

    assert status == "no_recipe"
    with get_session() as session:
        record = get_page_record(session, "https://test.com/about")
        assert record.status == "no_recipe"
        assert record.http_status == 200
        assert record.content_sha256 == hashlib.sha256(PAGE_CONTENT).hexdigest()
        assert record.fetched_at is not None
        assert session.scalars(select(Recipe)).first() is None


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.fetch_page")
def test_process_url_records_a_failed_fetch(mock_fetch: MagicMock) -> None:
    add_page("https://test.com/dead-link")
    mock_fetch.side_effect = FetchError("Can't fetch", status_code=404)

    status = process_url(get_page_model("https://test.com/dead-link"), SITE)

    assert status == "failed"
    with get_session() as session:
        record = get_page_record(session, "https://test.com/dead-link")
        assert record.status == "failed"
        assert record.http_status == 404
        assert record.error == "FetchError: Can't fetch"
        assert record.attempts == 1
        assert record.fetched_at is None
        assert record.content_sha256 is None


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.fetch_page")
def test_process_url_records_disallowed(mock_fetch: MagicMock) -> None:
    add_page("https://test.com/private")
    mock_fetch.side_effect = DisallowedError("robots.txt disallows '...'")

    status = process_url(get_page_model("https://test.com/private"), SITE)

    assert status == "disallowed"
    with get_session() as session:
        record = get_page_record(session, "https://test.com/private")
        assert record.status == "disallowed"
        assert record.http_status is None
        assert record.error == "DisallowedError: robots.txt disallows '...'"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.fetch_page")
def test_process_url_records_blocked_and_reraises(
    mock_fetch: MagicMock,
) -> None:
    add_page("https://test.com/walled")
    mock_fetch.side_effect = BlockedError("Blocked with HTTP 403", status_code=403)

    with pytest.raises(BlockedError):
        process_url(get_page_model("https://test.com/walled"), SITE)

    with get_session() as session:
        record = get_page_record(session, "https://test.com/walled")
        assert record.status == "blocked"
        assert record.http_status == 403
        assert record.attempts == 1


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.fetch_page")
def test_process_url_records_any_other_error_as_failed(
    mock_fetch: MagicMock,
) -> None:
    add_page("https://test.com/junk")
    mock_fetch.side_effect = ValueError("junk body")

    status = process_url(get_page_model("https://test.com/junk"), SITE)

    assert status == "failed"
    with get_session() as session:
        record = get_page_record(session, "https://test.com/junk")
        assert record.status == "failed"
        assert record.error == "ValueError: junk body"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.store_recipe_ingredients")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_a_failed_line_store_leaves_no_recipe(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
    mock_store: MagicMock,
) -> None:
    """The recipe and its lines are ONE transaction: if storing the
    lines raises, the recipe row is rolled back and the page is
    failed."""
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = (RECIPE_DATA, "json_ld")
    mock_store.side_effect = RuntimeError("line parse boom")

    status = process_url(get_page_model("https://test.com/recipes/cake"), SITE)

    assert status == "failed"
    with get_session() as session:
        record = get_page_record(session, "https://test.com/recipes/cake")
        assert record.status == "failed"
        assert record.error == "RuntimeError: line parse boom"
        assert record.attempts == 1
        assert record.content_sha256 == hashlib.sha256(PAGE_CONTENT).hexdigest()
        assert session.scalars(select(Recipe)).first() is None


# --- the recipe upsert ------------------------------------------------------


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_recipe_name_comes_from_json_ld(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
) -> None:
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = (RECIPE_DATA, "json_ld")

    process_url(get_page_model("https://test.com/recipes/cake"), SITE)

    with get_session() as session:
        recipe = session.scalars(select(Recipe)).first()
        assert recipe is not None
        assert recipe.name == "Cake"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_recipe_name_falls_back_to_the_page_title(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
) -> None:
    """Data that names nothing takes the page <title>."""
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = ({"@type": "Recipe"}, "microdata")

    process_url(get_page_model("https://test.com/recipes/cake"), SITE)

    with get_session() as session:
        recipe = session.scalars(select(Recipe)).first()
        assert recipe is not None
        assert recipe.name == "Page title"


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_recipe_name_falls_back_to_empty(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
) -> None:
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = b"<html><body></body></html>"
    mock_extract.return_value = ({"@type": "Recipe"}, "json_ld")

    process_url(get_page_model("https://test.com/recipes/cake"), SITE)

    with get_session() as session:
        recipe = session.scalars(select(Recipe)).first()
        assert recipe is not None
        assert recipe.name == ""


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.store_recipe_ingredients")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_unchanged_data_skips_storing_lines(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
    mock_store: MagicMock,
) -> None:
    """A page whose recipe data is unchanged does not re-store its
    lines; the page is still fetched."""
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = (RECIPE_DATA, "json_ld")

    cake = get_page_model("https://test.com/recipes/cake")
    assert process_url(cake, SITE) == "fetched"
    assert process_url(cake, SITE) == "fetched"

    assert mock_store.call_count == 1
    with get_session() as session:
        record = get_page_record(session, "https://test.com/recipes/cake")
        assert record.attempts == 2
        assert session.scalars(select(Recipe)).first() is not None


@pytest.mark.usefixtures("db_engine")
@patch("menu.ingest.pipeline.store_recipe_ingredients")
@patch("menu.ingest.pipeline.extract_recipe")
@patch("menu.ingest.pipeline.fetch_page")
def test_changed_data_replaces_the_recipe_and_lines(
    mock_fetch: MagicMock,
    mock_extract: MagicMock,
    mock_store: MagicMock,
) -> None:
    """Upstream changed the recipe: the same recipe row is updated and
    the lines are re-stored."""
    add_page("https://test.com/recipes/cake")
    mock_fetch.return_value = PAGE_CONTENT
    mock_extract.return_value = (RECIPE_DATA, "json_ld")

    process_url(get_page_model("https://test.com/recipes/cake"), SITE)
    mock_extract.return_value = ({"@type": "Recipe", "name": "Tart"}, "json_ld")
    process_url(get_page_model("https://test.com/recipes/cake"), SITE)

    assert mock_store.call_count == 2
    with get_session() as session:
        recipes = session.scalars(select(Recipe)).all()
        assert len(recipes) == 1
        assert recipes[0].name == "Tart"


# --- offline replay ---------------------------------------------------------


@pytest.mark.usefixtures("db_engine")
@patch.object(http_session(), "get")
def test_offline_crawl_leaves_an_uncached_page_queued(mock_get: MagicMock) -> None:
    """Offline a page the cache doesn't hold wasn't replayable: it
    stays queued with no attempt recorded, for the next live crawl."""
    add_page("https://test.com/recipes/cake")
    mock_get.side_effect = RuntimeError("network")

    report = crawl_sitemap(SITE, offline=True)

    assert report == CrawlReport()  # not cached: no attempt counted
    mock_get.assert_not_called()
    with get_session() as session:
        record = get_page_record(session, "https://test.com/recipes/cake")
        assert record.status == "queued"
        assert record.attempts == 0
        assert record.error is None


@pytest.mark.usefixtures("db_engine")
@patch.object(http_session(), "get")
def test_offline_crawl_leaves_an_uncached_sitemap_pending(
    mock_get: MagicMock,
) -> None:
    """Same rule for sitemaps: an uncached one stays pending instead
    of failing."""
    add_sitemap("https://test.com/sitemap1.xml")
    mock_get.side_effect = RuntimeError("network")

    report = crawl_sitemap(SITE, offline=True)

    assert report == CrawlReport()
    mock_get.assert_not_called()
    with get_session() as session:
        sitemap = session.scalars(select(Sitemap)).first()
        assert sitemap is not None
        assert sitemap.status == "pending"
        assert sitemap.error is None
        assert session.scalars(select(Page)).first() is None


# --- reparse -----------------------------------------------------------------


@pytest.mark.usefixtures("db_engine")
def test_reparse_site_only_touches_its_recipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reparse filters on the page's site: other sites' recipes keep
    their own parser once a second site lands."""
    with get_session() as session:
        bbc_page = Page(url="https://x/bbc", site="bbc_good_food")
        session.add(bbc_page)
        session.flush()
        session.add(
            Recipe(
                page_id=bbc_page.id,
                name="X",
                data={"recipeIngredient": ["25g butter"]},
                data_sha256="x",
                extractor="json_ld",
            )
        )
        other_page = Page(url="https://x/other", site="other_site")
        session.add(other_page)
        session.flush()
        session.add(
            Recipe(
                page_id=other_page.id,
                name="X",
                data={},
                data_sha256="x",
                extractor="json_ld",
            )
        )

    called: list[int] = []

    def fake_store(
        recipe_id: int,
        _recipe_data: dict[str, Any],
        _site: SiteConfig,
        _session: Any,
    ) -> None:
        called.append(recipe_id)

    monkeypatch.setattr("menu.ingest.pipeline.store_recipe_ingredients", fake_store)

    reparse_site(BBC_GOOD_FOOD)

    with get_session() as session:
        bbc_id = session.scalars(
            select(Recipe.id)
            .join(Page, Page.id == Recipe.page_id)
            .where(Page.url == "https://x/bbc")
        ).first()
        other_id = session.scalars(
            select(Recipe.id)
            .join(Page, Page.id == Recipe.page_id)
            .where(Page.url == "https://x/other")
        ).first()
    assert bbc_id is not None
    assert other_id is not None
    assert called == [bbc_id]
    assert called != [other_id]


def test_bbc_site_registered() -> None:
    assert get_site("bbc_good_food").name == BBC_GOOD_FOOD.name


class _HashEmbedder:
    model_id = "fake/hash"

    def embed(self, texts: list[str]) -> Any:
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4])
            rows.append(np.random.default_rng(seed).standard_normal(8))
        return np.array(rows, dtype=np.float32)


class _NotFoodClient:
    """Calls every item not_food; records what it was sent."""

    model_id = "fake/llm"

    def __init__(self) -> None:
        self.users: list[str] = []

    def complete(
        self, system: str, user: str, schema: dict[str, Any], *, max_tokens: int
    ) -> Any:
        del system, schema, max_tokens
        self.users.append(user)
        count = sum(1 for line in user.splitlines() if not line.startswith(" "))
        verdicts = [
            {
                "index": index,
                "kind": "not_food",
                "target": None,
                "variant": None,
                "new": None,
                "line": None,
                "name": None,
                "reason": "equipment",
            }
            for index in range(count)
        ]
        return json.dumps({"items": verdicts}), Usage(10, 5)


def _queue_terms() -> None:
    with get_session() as session:
        page = Page(url="https://x/r", site="bbc_good_food")
        session.add(page)
        session.flush()
        recipe = Recipe(
            page_id=page.id, name="R", data={}, data_sha256="r", extractor="json_ld"
        )
        session.add(recipe)
        session.flush()
        for position, term in enumerate(("bundt tin and tray", "cake tin or tray")):
            row = IngredientTerm(term=term, resolution="review")
            session.add(row)
            session.flush()
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=position,
                    raw_text=f"1 {term}",
                    term_id=row.id,
                )
            )


@pytest.mark.usefixtures("db_engine")
def test_suggest_appends_new_verdicts_and_skips_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue_terms()
    reviewed = suggestions.Suggestion(
        input="queue",
        kind="ok",
        subject="cake tin or tray",
        proposal=suggestions.OkProposal(),
        evidence={"reason": "earlier"},
        model={"llm": "human"},
        status="rejected",
    )
    suggestions.save([reviewed])
    client = _NotFoodClient()
    monkeypatch.setattr(pipeline, "suggest_embedder", _HashEmbedder)
    monkeypatch.setattr(pipeline, "suggest_client", lambda: client)

    pipeline.suggest_command(["queue"])

    assert len(client.users) == 1
    assert "bundt tin and tray" in client.users[0]
    assert "cake tin or tray" not in client.users[0]
    ledger = suggestions.load()
    assert [(s.subject, s.kind, s.status) for s in ledger] == [
        ("bundt tin and tray", "not_food", "pending"),
        ("cake tin or tray", "ok", "rejected"),
    ]
    assert ledger[0].model == {
        "llm": "fake/llm",
        "prompt": prompt_hash("queue"),
        "embedding": "fake/hash",
    }


@pytest.mark.usefixtures("db_engine")
def test_suggest_dry_run_calls_no_llm_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _queue_terms()

    def no_client() -> Any:
        raise AssertionError("dry run must not build an LLM client")

    monkeypatch.setattr(pipeline, "suggest_embedder", _HashEmbedder)
    monkeypatch.setattr(pipeline, "suggest_client", no_client)

    pipeline.suggest_command(["queue"], dry_run=True)

    out = capsys.readouterr().out
    assert "queue: 2 items" in out
    assert "Estimated token budget: 1300 tokens" in out
    assert suggestions.load() == []


# --- main(): subcommand dispatch ---------------------------------------------


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the shared preamble (initialise, seeding, sessions) so the
    dispatch tests never touch a database."""
    monkeypatch.setattr(pipeline, "initialise", MagicMock())
    monkeypatch.setattr(pipeline, "get_session", MagicMock())
    monkeypatch.setattr(pipeline, "seed_ingredient_data", MagicMock())


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.crawl_sitemap")
@patch("menu.ingest.pipeline.import_sitemap")
def test_main_crawl_imports_and_crawls(
    mock_import: MagicMock, mock_crawl: MagicMock
) -> None:
    main(["crawl", "bbc_good_food"])

    mock_import.assert_called_once_with(BBC_GOOD_FOOD)
    mock_crawl.assert_called_once_with(BBC_GOOD_FOOD)


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.crawl_sitemap")
@patch("menu.ingest.pipeline.import_sitemap")
def test_main_crawl_blocked_exits_cleanly(
    mock_import: MagicMock, mock_crawl: MagicMock
) -> None:
    mock_import.side_effect = BlockedError("Blocked with HTTP 402", status_code=402)

    with pytest.raises(SystemExit) as excinfo:
        main(["crawl", "bbc_good_food"])

    assert "bbc_good_food: blocked: Blocked with HTTP 402" in str(excinfo.value)
    mock_crawl.assert_not_called()


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.reparse_site")
def test_main_reparse_dispatches(mock_reparse: MagicMock) -> None:
    main(["reparse", "bbc_good_food"])

    mock_reparse.assert_called_once_with(BBC_GOOD_FOOD)


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.re_resolve", return_value=(1, 2, 3))
def test_main_re_resolve_prints_the_counts(
    mock_re_resolve: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["re-resolve"])

    mock_re_resolve.assert_called_once_with()
    assert (
        "Re-resolved: 1 terms changed, 2 lines regrammed, 3 concepts pruned"
        in capsys.readouterr().out
    )


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.suggest_command")
def test_main_suggest_dispatches_the_sets(mock_suggest: MagicMock) -> None:
    main(["suggest", "--sets", "queue,audit", "--limit", "5", "--dry-run"])

    mock_suggest.assert_called_once_with(["queue", "audit"], limit=5, dry_run=True)


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.suggest_command")
def test_main_suggest_defaults_to_all_four_sets(mock_suggest: MagicMock) -> None:
    main(["suggest"])

    mock_suggest.assert_called_once_with(
        ["queue", "audit", "pair", "food_ref"], limit=None, dry_run=False
    )


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.suggest_command")
def test_main_suggest_rejects_an_unknown_set(
    mock_suggest: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["suggest", "--sets", "queue,bogus"])

    assert excinfo.value.code == 2
    assert "unknown input set(s): bogus" in capsys.readouterr().err
    mock_suggest.assert_not_called()


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.suggest_command")
def test_main_suggest_rejects_a_zero_limit(
    mock_suggest: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["suggest", "--limit", "0"])

    assert excinfo.value.code == 2
    assert "must be at least 1" in capsys.readouterr().err
    mock_suggest.assert_not_called()


@pytest.mark.usefixtures("no_db")
def test_main_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.derive_all_nutrition", return_value=7)
def test_main_derive_nutrition_prints_the_count(
    mock_derive: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["derive-nutrition"])

    mock_derive.assert_called_once_with()
    assert "Derived nutrition for 7 recipes" in capsys.readouterr().out


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.fdc_id_conflicts")
def test_main_report_fdc_conflicts_prints_each_food(
    mock_conflicts: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_conflicts.return_value = [("Butter", ["butter", "sweet butter"])]

    main(["report", "fdc-conflicts"])

    assert "Butter: butter, sweet butter" in capsys.readouterr().out


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.suggest_embedder")
@patch("menu.ingest.pipeline.ref_review")
def test_main_report_ref_review_prints_the_rows(
    mock_review: MagicMock,
    mock_embedder: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_review.return_value = [
        (5, "milk", "whole milk", ["mlk"], ["milk ref", "cream"]),
        (3, "eggs", None, [], []),
    ]

    main(["report", "ref-review"])

    mock_embedder.assert_called_once_with()
    out = capsys.readouterr().out
    assert "    5  milk: parser candidate: whole milk | mlk" in out
    assert "    milk ref | cream" in out
    assert "    3  eggs: no parser candidate" in out


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.validation_report")
def test_main_report_validate_nutrition_dispatches(mock_report: MagicMock) -> None:
    main(["report", "validate-nutrition"])

    mock_report.assert_called_once_with()


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.coverage_report")
def test_main_report_coverage_dispatches(mock_report: MagicMock) -> None:
    main(["report", "coverage"])

    mock_report.assert_called_once_with()


@pytest.mark.usefixtures("no_db")
@patch("menu.ingest.pipeline.collect_metrics", return_value={"recipes": 3})
def test_main_metrics_writes_the_snapshot(
    mock_metrics: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "snapshots" / "metrics.json"

    main(["metrics", str(path)])

    mock_metrics.assert_called_once_with()
    assert path.read_text() == '{\n  "recipes": 3\n}\n'
    assert f"Metrics snapshot written to {path}" in capsys.readouterr().out
