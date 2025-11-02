from unittest.mock import MagicMock, patch

from menu.db.sitemap.actions import finalise_sitemap


@patch("menu.db.sitemap.actions.get_session")
def test_finalise_sitemap(mock_get_session):
    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_session.query.return_value = mock_query
    mock_get_session.return_value.__enter__.return_value = mock_session

    finalise_sitemap("http://test.com")

    mock_session.query.assert_called_once()
    mock_query.filter.assert_called_once()
    mock_query.filter.return_value.update.assert_called_once_with({"completed": True})
