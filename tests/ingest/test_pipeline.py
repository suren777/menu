"""Pipeline tests: the crawl ledger and the atomic recipe write."""

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import Page, Recipe, Sitemap
from menu.db.pages import repository as pages_repository
from menu.db.sitemaps import actions as sitemap_actions
from menu.ingest.discover import SitemapEntry
from menu.ingest.errors import (
    BlockedError,
    DisallowedError,
    FetchError,
)
from menu.ingest.pipeline import (
    CrawlReport,
    crawl_sitemap,
    import_sitemap,
    process_url,
    reparse_site,
)
from menu.ingest.registry import SiteConfig, get_site
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

if TYPE_CHECKING:
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
@patch("menu.ingest.http.session.get")
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
@patch("menu.ingest.http.session.get")
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
