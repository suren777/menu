"""BBC Good Food site config."""

from menu.ingest.registry import SiteConfig

BBC_SITEMAP = "https://www.bbcgoodfood.com/sitemap.xml"
BBC_JSON_TEST_ID = "page-schema"

BBC_GOOD_FOOD = SiteConfig(
    name="bbc_good_food",
    base_url="https://www.bbcgoodfood.com",
    sitemap_urls=(BBC_SITEMAP,),
    url_pattern=r"^https://www\.bbcgoodfood\.com/recipes/[^/]+$",
    politeness_delay=1.0,
    json_ld_test_id=BBC_JSON_TEST_ID,
    unit_system="imperial",
)
