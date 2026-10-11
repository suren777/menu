"""Page and recipe queries: plain functions over a caller-supplied
session.

The repository owns the crawl ledger (pages) and the recipes captured
from it — a recipe is reached through its page.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, select

from menu.db.database import Page, Recipe

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


@dataclass
class PageModel:
    id: int
    site: str
    url: str
    sitemap_id: int | None
    lastmod: str | None
    status: str
    http_status: int | None
    attempts: int
    fetched_at: datetime | None
    content_sha256: str | None
    error: str | None


def to_model(record: Page) -> PageModel:
    return PageModel(
        id=record.id,
        site=record.site,
        url=record.url,
        sitemap_id=record.sitemap_id,
        lastmod=record.lastmod,
        status=record.status,
        http_status=record.http_status,
        attempts=record.attempts,
        fetched_at=record.fetched_at,
        content_sha256=record.content_sha256,
        error=record.error,
    )


def find_by_url(url: str, session: Session) -> PageModel | None:
    record = session.scalars(select(Page).where(Page.url == url)).first()
    return to_model(record) if record is not None else None


def get_all(session: Session) -> list[PageModel]:
    return [to_model(record) for record in session.scalars(select(Page))]


def page_exists(url: str, session: Session) -> bool:
    return bool(session.scalar(select(exists().where(Page.url == url))))


def get_unfinished(session: Session, site: str) -> list[PageModel]:
    """The site's pages still to fetch: queued, or blocked by the last
    attempt — a block is retried. In id order."""
    return [
        to_model(record)
        for record in session.scalars(
            select(Page)
            .where(Page.site == site, Page.status.in_(("queued", "blocked")))
            .order_by(Page.id)
        )
    ]


@dataclass
class RecipeModel:
    id: int
    page_id: int
    name: str
    data: dict[str, Any]
    data_sha256: str
    extractor: str
    servings: int | None
    yield_text: str | None


def to_recipe_model(record: Recipe) -> RecipeModel:
    return RecipeModel(
        id=record.id,
        page_id=record.page_id,
        name=record.name,
        data=dict(record.data),
        data_sha256=record.data_sha256,
        extractor=record.extractor,
        servings=record.servings,
        yield_text=record.yield_text,
    )


def data_sha256(data: dict[str, Any]) -> str:
    """Fingerprint of the captured JSON: sorts keys and keeps
    non-ASCII as-is, so the same payload always hashes the same."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def upsert_recipe(
    page_id: int,
    name: str,
    data: dict[str, Any],
    extractor: str,
    session: Session,
) -> RecipeModel | None:
    """Insert or update the page's recipe, inside the caller's
    transaction.

    Returns the recipe when it was created or its data changed (its
    lines must be re-stored), None when the captured data is unchanged
    (nothing to do — the stored lines stay as they are).
    """
    sha = data_sha256(data)
    record = session.scalars(select(Recipe).where(Recipe.page_id == page_id)).first()
    if record is None:
        record = Recipe(
            page_id=page_id,
            name=name,
            data=data,
            data_sha256=sha,
            extractor=extractor,
        )
        session.add(record)
        session.flush()
        return to_recipe_model(record)
    if record.data_sha256 == sha:
        return None
    record.data = data
    record.name = name
    record.extractor = extractor
    record.data_sha256 = sha
    session.flush()
    return to_recipe_model(record)


def get_site_recipes(site: str, session: Session) -> list[tuple[RecipeModel, str]]:
    """The site's recipes with their page URLs (a reparse reads them)."""
    rows = session.execute(
        select(Recipe, Page.url)
        .join(Page, Page.id == Recipe.page_id)
        .where(Page.site == site)
        .order_by(Recipe.id)
    ).all()
    return [(to_recipe_model(record), url) for record, url in rows]
