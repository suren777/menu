import json
from multiprocessing import Pool

from pydantic import BaseModel

from menu.crawlers.bbc_good_food.const import BBC_JSON_TEST_ID, BBC_SITEMAP
from menu.crawlers.bbc_good_food.utils import (
    contains_recipe,
    fetch_recipe,
    get_sitemap,
    request_xml,
)
from menu.db.connection import get_session
from menu.db.database import Sitemap, initialise
from menu.db.recipie_urls.actions import add_recipe
from menu.db.sitemap.actions import finalise_sitemap
from menu.db.sitemap.repository import SitemapRepository


class Crawler(BaseModel):
    def __init__(self, url: str):
        self.url = url


def import_sitemap():
    sitemap = get_sitemap(BBC_SITEMAP)

    with get_session() as session:
        for url in sitemap:
            if not SitemapRepository.url_exists(url, session):
                session.add(Sitemap(url=url))


def import_recipes(url: str) -> None:
    for recipe_url in request_xml(url):
        recipe = fetch_recipe(recipe_url)
        if contains_recipe(recipe):
            script_tag = recipe.find("script", {"data-testid": BBC_JSON_TEST_ID})
            if script_tag:
                script_contents = getattr(script_tag, "contents", [])
                if script_contents:
                    recipe_data = json.loads(script_contents[0])
                    name = recipe.title.text if recipe.title else ""
                    add_recipe(recipe_url, name, recipe_data)
    finalise_sitemap(url)


def crawl_sitemap() -> None:
    with get_session() as session:
        urls = (sitemap.url for sitemap in SitemapRepository.get_unfinished(session))

    pool = Pool(processes=4)
    pool.map(import_recipes, urls)


if __name__ == "__main__":
    initialise()
    import_sitemap()
    crawl_sitemap()
