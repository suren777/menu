"""BBC Good Food site config and site-specific extraction helpers."""

from bs4 import BeautifulSoup, Tag

from menu.ingest.registry import SiteConfig

BBC_SITEMAP = "https://www.bbcgoodfood.com/sitemap.xml"
BBC_JSON_TEST_ID = "page-schema"
BBC_BREADCRUMB_CLASS = "breadcrumb__list body-copy-extra-small oflow-x-auto list"

BBC_GOOD_FOOD = SiteConfig(
    name="bbc_good_food",
    base_url="https://www.bbcgoodfood.com",
    sitemap_url=BBC_SITEMAP,
    url_pattern=r"^https://www\.bbcgoodfood\.com/recipes/[^/]+$",
    politeness_delay=1.0,
    json_ld_test_id=BBC_JSON_TEST_ID,
)


def contains_recipe(content: BeautifulSoup) -> bool:
    """BBC-specific check: recipe pages have a breadcrumb whose second
    entry is 'Recipes' and whose third is not 'Collection'."""
    ul = content.find("ul", {"class": BBC_BREADCRUMB_CLASS})
    if not isinstance(ul, Tag):
        return False

    li = ul.find_all("li")
    return len(li) > 2 and li[1].text == "Recipes" and li[2].text != "Collection"
