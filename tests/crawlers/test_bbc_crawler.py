"""Tests for BBC Good Food crawler."""

from unittest.mock import patch

from sqlalchemy.orm import Session

from menu.crawlers.bbc_good_food.crawler import import_sitemap


def test_import_sitemap(session: Session):
    """Test import_sitemap function."""
    sitemap = ["test"]
    with patch("menu.crawlers.bbc_good_food.crawler.get_sitemap", return_value=sitemap):
        with patch(
            "menu.crawlers.bbc_good_food.crawler.get_session", return_value=session
        ):
            import_sitemap()
