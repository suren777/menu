import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.ext import ConversationHandler

from menu.menu_bot.generate_recipe_conversation import (
    start,
    category_callback,
    recipe_selection,
)
from menu.menu_bot.helpers import ConversationStages

@pytest.mark.asyncio
async def test_start():
    update = MagicMock()
    update.message = AsyncMock()
    context = MagicMock()
    result = await start(update, context)
    update.message.reply_text.assert_called_once()
    assert result == ConversationStages.MENU_TYPE.value

@pytest.mark.asyncio
async def test_start_no_message():
    update = MagicMock()
    update.message = None
    context = MagicMock()
    result = await start(update, context)
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.generate_recipe_conversation.get_cuisines', return_value=['italian', 'mexican'])
async def test_category_callback(mock_get_cuisines):
    update = MagicMock()
    query = AsyncMock()
    query.data = "dessert"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}
    result = await category_callback(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    assert result == ConversationStages.CUISINE.value

@pytest.mark.asyncio
@patch('menu.menu_bot.generate_recipe_conversation.get_cuisines', return_value=['italian', 'mexican'])
async def test_category_callback_no_user_data(mock_get_cuisines):
    update = MagicMock()
    query = AsyncMock()
    query.data = "dessert"
    update.callback_query = query
    context = MagicMock()
    context.user_data = None
    result = await category_callback(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    assert result == ConversationStages.CUISINE.value

@pytest.mark.asyncio
async def test_category_callback_no_query():
    update = MagicMock()
    update.callback_query = None
    context = MagicMock()
    result = await category_callback(update, context)
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.generate_recipe_conversation.get_cuisines', return_value=[])
async def test_category_callback_no_cuisines(mock_get_cuisines):
    update = MagicMock()
    query = AsyncMock()
    query.data = "dessert"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}
    result = await category_callback(update, context)
    assert result == ConversationStages.SUMMARY.value

@pytest.mark.asyncio
@patch('menu.menu_bot.generate_recipe_conversation.get_recipe_names', return_value=[{'name': 'recipe1', 'id': 1}])
async def test_recipe_selection(mock_get_recipe_names):
    update = MagicMock()
    query = AsyncMock()
    query.data = "italian"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"category": "dessert"}
    result = await recipe_selection(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    assert result == ConversationStages.SUMMARY.value

@pytest.mark.asyncio
@patch('menu.menu_bot.generate_recipe_conversation.get_recipe_names', return_value=[{'name': 'recipe1', 'id': 1}])
async def test_recipe_selection_no_user_data(mock_get_recipe_names):
    update = MagicMock()
    query = AsyncMock()
    query.data = "italian"
    update.callback_query = query
    context = MagicMock()
    context.user_data = None
    result = await recipe_selection(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    assert result == ConversationStages.SUMMARY.value

@pytest.mark.asyncio
async def test_recipe_selection_no_query():
    update = MagicMock()
    update.callback_query = None
    context = MagicMock()
    result = await recipe_selection(update, context)
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.generate_recipe_conversation.get_recipe_names', return_value=[])
async def test_recipe_selection_no_recipes(mock_get_recipe_names):
    update = MagicMock()
    query = AsyncMock()
    query.data = "italian"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"category": "dessert"}
    result = await recipe_selection(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once_with("Can't find anything to cook")
    assert result == ConversationHandler.END
