from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from menu.menu_bot.find_ingredients_conversation import (
    ONE,
    TWO,
    search_for_recipes_by_ingredients,
    start,
)
from menu.menu_bot.helpers import MAX_OPTIONS

# Mark all tests in this module as asyncio
pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_update():
    """Fixture for a mock telegram.Update object."""
    update = MagicMock()
    update.message = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    """Fixture for a mock telegram.ext.ContextTypes.DEFAULT_TYPE object."""
    context = MagicMock()
    context.user_data = {}
    return context


async def test_start(mock_update, mock_context):
    """Test the start function of the conversation."""
    # Act
    result = await start(mock_update, mock_context)

    # Assert
    mock_update.message.reply_text.assert_awaited_once_with(
        "<b>Welcome to the Food Recipe Bot!\n"
        "Please enter a list of ingredients, separated by commas.</b>",
        parse_mode="HTML",
    )
    assert result == ONE


@patch("menu.menu_bot.find_ingredients_conversation.search_recipes_by_ingredients")
async def test_search_for_recipes_by_ingredients_found(
    mock_search, mock_update, mock_context
):
    """Test searching for recipes when some are found."""
    # Arrange
    mock_update.message.text = "onion, garlic"
    mock_recipes = [{"name": "Pasta", "id": 1}, {"name": "Soup", "id": 2}]
    mock_search.return_value = mock_recipes

    # Act
    with patch(
        "menu.menu_bot.find_ingredients_conversation.inline_keyboard_generator_from_dict"
    ) as mock_keyboard_gen:
        mock_keyboard_gen.return_value = [[]]  # dummy keyboard
        result = await search_for_recipes_by_ingredients(mock_update, mock_context)

    # Assert
    mock_search.assert_called_once_with(["onion", " garlic"])
    mock_update.message.reply_text.assert_awaited_once()
    call_args = mock_update.message.reply_text.await_args
    assert (
        "<b>I have found a few recipes, which one do you want to cook?</b>"
        in call_args.args[0]
    )
    assert result == TWO


@patch("menu.menu_bot.find_ingredients_conversation.search_recipes_by_ingredients")
async def test_search_for_recipes_by_ingredients_not_found(
    mock_search, mock_update, mock_context
):
    """Test searching for recipes when none are found."""
    # Arrange
    mock_update.message.text = "nonexistent, ingredient"
    mock_search.return_value = []

    # Act
    result = await search_for_recipes_by_ingredients(mock_update, mock_context)

    # Assert
    mock_search.assert_called_once_with(["nonexistent", " ingredient"])
    mock_update.message.reply_text.assert_awaited_once_with(
        "Can't find anything to cook with these ingredients, try searching for something else"
    )
    assert result == ConversationHandler.END


@patch("menu.menu_bot.find_ingredients_conversation.random.sample")
@patch("menu.menu_bot.find_ingredients_conversation.search_recipes_by_ingredients")
async def test_search_for_recipes_by_ingredients_too_many_found(
    mock_search, mock_random_sample, mock_update, mock_context
):
    """Test that the recipe list is sampled if too many are found."""
    # Arrange
    mock_update.message.text = "flour"
    long_recipe_list = [
        {"name": f"Recipe {i}", "id": i} for i in range(MAX_OPTIONS + 5)
    ]
    mock_search.return_value = long_recipe_list
    sampled_list = long_recipe_list[:MAX_OPTIONS]
    mock_random_sample.return_value = sampled_list

    # Act
    with patch(
        "menu.menu_bot.find_ingredients_conversation.inline_keyboard_generator_from_dict"
    ) as mock_keyboard_gen:
        mock_keyboard_gen.return_value = [[]]
        result = await search_for_recipes_by_ingredients(mock_update, mock_context)

    # Assert
    mock_search.assert_called_once_with(["flour"])
    mock_random_sample.assert_called_once_with(long_recipe_list, k=MAX_OPTIONS)
    mock_keyboard_gen.assert_called_once_with(sampled_list, "name", "id")
    mock_update.message.reply_text.assert_awaited_once()
    assert result == TWO
