# tests/bot/test_find_recipe_conversation.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update
from telegram.ext import ConversationHandler, ContextTypes

from menu.menu_bot.find_recipe_conversation import (
    start,
    search_for_recipes,
    ONE,
    TWO,
)
from menu.menu_bot.helpers import MAX_OPTIONS


@pytest.mark.asyncio
async def test_start():
    """Test the start function to ensure it sends the correct message and returns the correct state."""
    update = MagicMock()
    update.message = AsyncMock()
    update.message.from_user.id = 123
    context = MagicMock()

    result = await start(update, context)

    update.message.reply_text.assert_called_once_with(
        "<b>Welcome to the Food Recipe Bot!\nWhat Recipe do you want to search for?</b>",
        parse_mode="HTML",
    )
    assert result == ONE


@pytest.mark.asyncio
@patch("menu.menu_bot.find_recipe_conversation.search_recipe_by_name")
async def test_search_for_recipes_found_less_than_max(mock_search_recipe):
    """Test search_for_recipes when recipes are found (less than MAX_OPTIONS)."""
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test"
    context = MagicMock()
    recipes = [{"name": "recipe1", "id": 1}, {"name": "recipe2", "id": 2}]
    mock_search_recipe.return_value = recipes

    result = await search_for_recipes(update, context)

    update.message.reply_text.assert_called_once()
    assert result == TWO


@pytest.mark.asyncio
@patch("menu.menu_bot.find_recipe_conversation.search_recipe_by_name")
async def test_search_for_recipes_found_more_than_max(mock_search_recipe):
    """Test search_for_recipes when more than MAX_OPTIONS recipes are found."""
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test"
    context = MagicMock()
    recipes = [{"name": f"recipe{i}", "id": i} for i in range(MAX_OPTIONS + 5)]
    mock_search_recipe.return_value = recipes

    with patch("random.sample", return_value=recipes[:MAX_OPTIONS]) as mock_sample:
        result = await search_for_recipes(update, context)
        mock_sample.assert_called_once_with(recipes, k=MAX_OPTIONS)

    update.message.reply_text.assert_called_once()
    assert result == TWO


@pytest.mark.asyncio
@patch("menu.menu_bot.find_recipe_conversation.search_recipe_by_name")
async def test_search_for_recipes_not_found(mock_search_recipe):
    """Test search_for_recipes when no recipes are found."""
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test"
    context = MagicMock()
    mock_search_recipe.return_value = []

    result = await search_for_recipes(update, context)

    update.message.reply_text.assert_called_with(
        "Can't find anything to cook, try searching for something else"
    )
    assert result == ConversationHandler.END



@pytest.mark.asyncio
async def test_conversation_entry_points():
    """Check if the conversation handler is correctly set up with the /search command."""
    from menu.menu_bot.find_recipe_conversation import find_recipes_conversation

    assert len(find_recipes_conversation.entry_points) == 1
    handler = find_recipes_conversation.entry_points[0]
    assert handler.callback == start


@pytest.mark.asyncio
async def test_conversation_states():
    """Check if the states ONE and TWO are correctly configured."""
    from menu.menu_bot.find_recipe_conversation import find_recipes_conversation

    assert ONE in find_recipes_conversation.states
    assert TWO in find_recipes_conversation.states
    assert len(find_recipes_conversation.states[ONE]) == 1
    assert len(find_recipes_conversation.states[TWO]) == 1


@pytest.mark.asyncio
async def test_conversation_fallbacks():
    """Check if the /cancel command is correctly set up as a fallback."""
    from menu.menu_bot.find_recipe_conversation import find_recipes_conversation
    from menu.menu_bot.common import cancel

    assert len(find_recipes_conversation.fallbacks) == 1
    handler = find_recipes_conversation.fallbacks[0]
    assert handler.callback == cancel


@pytest.mark.asyncio
async def test_summary_callback():
    """Test that the summary function is called in state TWO."""
    from menu.menu_bot.find_recipe_conversation import find_recipes_conversation
    from menu.menu_bot.common import summary

    handler = find_recipes_conversation.states[TWO][0]
    assert handler.callback == summary

