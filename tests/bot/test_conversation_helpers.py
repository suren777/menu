import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from menu.menu_bot.conversation_helpers import (
    start,
    category_callback,
    recipe_selection,
    search_for_recipes,
    search_for_recipes_by_ingredients,
    summary,
    cancel,
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
@patch('menu.menu_bot.conversation_helpers.get_cuisines', return_value=['italian', 'mexican'])
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
@patch('menu.menu_bot.conversation_helpers.get_cuisines', return_value=['italian', 'mexican'])
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
@patch('menu.menu_bot.conversation_helpers.get_cuisines', return_value=[])
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
@patch('menu.menu_bot.conversation_helpers.get_recipe_names', return_value=[{'name': 'recipe1', 'id': 1}])
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
@patch('menu.menu_bot.conversation_helpers.get_recipe_names', return_value=[{'name': 'recipe1', 'id': 1}])
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
@patch('menu.menu_bot.conversation_helpers.get_recipe_names', return_value=[])
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

@pytest.mark.asyncio
@patch('menu.menu_bot.conversation_helpers.search_recipe_by_name', return_value=[{'name': 'recipe1', 'id': 1}])
async def test_search_for_recipes(mock_search_recipe):
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test"
    context = MagicMock()
    result = await search_for_recipes(update, context)
    update.message.reply_text.assert_called_once()
    assert result == 1

@pytest.mark.asyncio
async def test_search_for_recipes_no_message():
    update = MagicMock()
    update.message = None
    context = MagicMock()
    result = await search_for_recipes(update, context)
    assert result == ConversationHandler.END

@pytest.mark.asyncio
async def test_search_for_recipes_no_text():
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = None
    context = MagicMock()
    result = await search_for_recipes(update, context)
    update.message.reply_text.assert_called_once_with("Please provide a search query.")
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.conversation_helpers.search_recipe_by_name', return_value=[])
async def test_search_for_recipes_no_results(mock_search_recipe):
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test"
    context = MagicMock()
    result = await search_for_recipes(update, context)
    update.message.reply_text.assert_called_once_with("Can't find anything to cook, try searching for something else")
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.conversation_helpers.search_recipes_by_ingredients', return_value=[{'name': 'recipe1', 'id': 1}])
async def test_search_for_recipes_by_ingredients(mock_search_recipes):
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "ingredient1,ingredient2"
    context = MagicMock()
    result = await search_for_recipes_by_ingredients(update, context)
    update.message.reply_text.assert_called_once()
    assert result == 1

@pytest.mark.asyncio
async def test_search_for_recipes_by_ingredients_no_message():
    update = MagicMock()
    update.message = None
    context = MagicMock()
    result = await search_for_recipes_by_ingredients(update, context)
    assert result == ConversationHandler.END

@pytest.mark.asyncio
async def test_search_for_recipes_by_ingredients_no_text():
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = None
    context = MagicMock()
    result = await search_for_recipes_by_ingredients(update, context)
    update.message.reply_text.assert_called_once_with("Please provide a list of ingredients.")
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.conversation_helpers.search_recipes_by_ingredients', return_value=[])
async def test_search_for_recipes_by_ingredients_no_results(mock_search_recipes):
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "ingredient1,ingredient2"
    context = MagicMock()
    result = await search_for_recipes_by_ingredients(update, context)
    update.message.reply_text.assert_called_once_with("Can't find anything to cook with these ingredients, try searching for something else")
    assert result == ConversationHandler.END

@pytest.mark.asyncio
@patch('menu.menu_bot.conversation_helpers.get_recipe_by_id', return_value="Test Recipe Details")
async def test_summary(mock_get_recipe):
    update = MagicMock()
    query = AsyncMock()
    query.data = "1"
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"cuisine": "italian", "category": "dessert"}
    result = await summary(update, context)
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    assert result == ConversationHandler.END

@pytest.mark.asyncio
async def test_cancel():
    update = MagicMock()
    context = MagicMock()
    result = await cancel(update, context)
    assert result == ConversationHandler.END
