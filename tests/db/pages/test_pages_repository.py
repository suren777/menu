import hashlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from menu.db.database import Page, Recipe, Sitemap
from menu.db.pages import repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

RECIPE_DATA: dict[str, Any] = {"key": "value"}


def add_page(url: str, site: str = "test", status: str = "queued") -> Page:
    return Page(url=url, site=site, status=status)


def test_to_model(session: Session) -> None:
    sitemap = Sitemap(url="http://test.com/sitemap.xml", site="test")
    session.add(sitemap)
    session.flush()
    page = add_page("http://test.com")
    page.sitemap_id = sitemap.id
    page.lastmod = "2026-10-01"
    page.attempts = 2
    session.add(page)
    session.flush()

    model = repository.to_model(page)

    assert model.id == page.id
    assert model.url == "http://test.com"
    assert model.site == "test"
    assert model.sitemap_id == sitemap.id
    assert model.lastmod == "2026-10-01"
    assert model.status == "queued"
    assert model.attempts == 2


def test_find_by_url(session: Session) -> None:
    session.add(add_page("http://test.com"))
    session.flush()

    model = repository.find_by_url("http://test.com", session)

    assert model is not None
    assert model.url == "http://test.com"


def test_find_by_url_missing(session: Session) -> None:
    assert repository.find_by_url("http://missing.com", session) is None


def test_get_all(session: Session) -> None:
    session.add(add_page("http://test.com"))
    session.add(add_page("http://other.com"))
    session.flush()

    models = repository.get_all(session)

    assert {model.url for model in models} == {"http://test.com", "http://other.com"}


def test_page_exists(session: Session) -> None:
    session.add(add_page("http://test.com"))
    session.flush()

    assert repository.page_exists("http://test.com", session)
    assert not repository.page_exists("http://missing.com", session)


def test_get_unfinished_takes_queued_and_blocked_in_id_order(session: Session) -> None:
    """A blocked page is retried on the next run: it is unfinished.
    Fetched, no_recipe and failed pages are left alone."""
    first = add_page("http://queued.com", status="queued")
    second = add_page("http://blocked.com", status="blocked")
    session.add(first)
    session.add(second)
    session.add(add_page("http://fetched.com", status="fetched"))
    session.add(add_page("http://norecipe.com", status="no_recipe"))
    session.add(add_page("http://failed.com", status="failed"))
    session.add(add_page("http://other-site.com", site="other"))
    session.flush()

    models = repository.get_unfinished(session, "test")

    assert [model.url for model in models] == [
        "http://queued.com",
        "http://blocked.com",
    ]


def test_data_sha256_is_canonical_json() -> None:
    assert repository.data_sha256({"b": 2, "a": 1}) == hashlib.sha256(
        b'{"a": 1, "b": 2}'
    ).hexdigest()


def test_upsert_recipe_inserts(session: Session) -> None:
    page = add_page("http://test.com")
    session.add(page)
    session.flush()

    model = repository.upsert_recipe(
        page.id, "Test Recipe", RECIPE_DATA, "json_ld", session
    )

    assert model is not None
    assert model.id is not None
    assert model.page_id == page.id
    assert model.name == "Test Recipe"
    assert model.data == RECIPE_DATA
    assert model.extractor == "json_ld"
    assert model.data_sha256 == repository.data_sha256(RECIPE_DATA)


def test_upsert_recipe_unchanged_data_does_nothing(session: Session) -> None:
    """The same data twice: the second upsert returns None and the
    stored row keeps the first write — its lines stay as they are."""
    page = add_page("http://test.com")
    session.add(page)
    session.flush()
    first = repository.upsert_recipe(
        page.id, "Test Recipe", RECIPE_DATA, "json_ld", session
    )
    assert first is not None

    second = repository.upsert_recipe(
        page.id, "Renamed Recipe", RECIPE_DATA, "microdata", session
    )

    assert second is None
    current = session.scalars(
        select(Recipe).where(Recipe.page_id == page.id)
    ).first()
    assert current is not None
    assert current.name == "Test Recipe"
    assert current.extractor == "json_ld"


def test_upsert_recipe_changed_data_updates(session: Session) -> None:
    """Changed upstream data: same recipe row, new data, name,
    extractor and sha — the caller re-stores the lines."""
    page = add_page("http://test.com")
    session.add(page)
    session.flush()
    first = repository.upsert_recipe(
        page.id, "Test Recipe", RECIPE_DATA, "json_ld", session
    )
    assert first is not None

    updated = repository.upsert_recipe(
        page.id, "Renamed Recipe", {"key": "new"}, "microdata", session
    )

    assert updated is not None
    assert updated.id == first.id
    assert updated.name == "Renamed Recipe"
    assert updated.data == {"key": "new"}
    assert updated.extractor == "microdata"
    assert updated.data_sha256 == repository.data_sha256({"key": "new"})


def test_get_site_recipes_joins_the_page_url(session: Session) -> None:
    page = add_page("http://test.com", site="bbc_good_food")
    other = add_page("http://other.com", site="other_site")
    session.add_all([page, other])
    session.flush()
    repository.upsert_recipe(page.id, "Test", RECIPE_DATA, "json_ld", session)
    repository.upsert_recipe(other.id, "Other", {}, "json_ld", session)

    recipes = repository.get_site_recipes("bbc_good_food", session)

    assert len(recipes) == 1
    recipe, url = recipes[0]
    assert url == "http://test.com"
    assert recipe.data == RECIPE_DATA
