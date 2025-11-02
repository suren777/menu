from unittest.mock import patch, MagicMock
from menu.db.recipie_urls.actions import add_recipe

@patch('menu.db.recipie_urls.actions.get_session')
@patch('menu.db.recipie_urls.actions.RecipeUrlsRepository')
def test_add_recipe(mock_repo, mock_get_session):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    recipe_data = {"key": "value"}

    add_recipe("http://test.com", "Test Recipe", recipe_data)

    mock_repo.add_recipe.assert_called_once_with(
        url="http://test.com", name="Test Recipe", recipe_data=recipe_data, session=mock_session
    )
