import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.ext import ConversationHandler

from menu.menu_bot.common import summary, cancel

@pytest.mark.asyncio
async def test_summary_no_query():
    update = MagicMock()
    update.callback_query = None
    context = MagicMock()
    result = await summary(update, context)
    assert result == ConversationHandler.END

@pytest.mark.asyncio
async def test_summary_invalid_recipe_id():
    update = MagicMock()
    query = AsyncMock()
    query.data = "invalid"
    update.callback_query = query
    context = MagicMock()
    result = await summary(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once_with("Invalid recipe ID.")
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.common.get_recipe_by_id', return_value=None)
async def test_summary_recipe_not_found(mock_get_recipe):
    update = MagicMock()
    query = AsyncMock()
    query.data = "1"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"cuisine": "italian", "category": "dessert"}
    result = await summary(update, context)
    query.answer.assert_called_once()
    mock_get_recipe.assert_called_once_with(1)
    query.edit_message_text.assert_called_once_with("Can't find anything to cook")
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.common.get_recipe_by_id', return_value="Test Recipe Details")
async def test_summary_success_with_user_data(mock_get_recipe):
    update = MagicMock()
    query = AsyncMock()
    query.data = "1"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"cuisine": "italian", "category": "dessert"}
    result = await summary(update, context)
    expected_message = "Cuisine: italian\nCategory: Dessert\nTest Recipe Details"

    query.answer.assert_called_once()
    mock_get_recipe.assert_called_once_with(1)
    query.edit_message_text.assert_called_once_with(
        expected_message,
        parse_mode="HTML",
    )
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.common.get_recipe_by_id', return_value="Test Recipe Details")
async def test_summary_success_without_user_data(mock_get_recipe):
    update = MagicMock()
    query = AsyncMock()
    query.data = "1"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}
    result = await summary(update, context)
    expected_message = "Test Recipe Details"

    query.answer.assert_called_once()
    mock_get_recipe.assert_called_once_with(1)
    query.edit_message_text.assert_called_once_with(
        expected_message,
        parse_mode="HTML",
    )
    assert result == ConversationHandler.END

@pytest.mark.asyncio
async def test_cancel():
    update = MagicMock()
    context = MagicMock()
    result = await cancel(update, context)
    assert result == ConversationHandler.END
