"""Tests for the quick menu conversation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from menu.menu_bot.conversation_helpers import (
    category_callback,
    recipe_selection,
    start_quick_recipe,
)
from menu.menu_bot.helpers import MAIN_CATEGORIES, ConversationStages

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_update():
    """Fixture for a mock telegram.Update object."""
    update = MagicMock()
    update.message = AsyncMock()
    update.callback_query = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    """Fixture for a mock telegram.ext.ContextTypes.DEFAULT_TYPE object."""
    context = MagicMock()
    context.user_data = {}
    return context


async def test_start_quick_recipe(mock_update: MagicMock, mock_context: MagicMock):
    """Test the start function of the quick recipe conversation."""
    with patch(
        "menu.menu_bot.conversation_helpers.inline_keyboard_generator"
    ) as mock_keyboard_gen:
        mock_keyboard_gen.return_value = [[]]  # dummy keyboard
        result = await start_quick_recipe(mock_update, mock_context)

    mock_keyboard_gen.assert_called_once_with(MAIN_CATEGORIES)
    mock_update.message.reply_text.assert_awaited_once()
    call_args = mock_update.message.reply_text.await_args
    assert "I understand that you are in a hurry" in call_args.args[0]
    assert result == ConversationStages.MENU_TYPE.value


@patch("menu.menu_bot.conversation_helpers.get_cuisines")
async def test_category_callback_with_cuisines(
    mock_get_cuisines: MagicMock, mock_update: MagicMock, mock_context: MagicMock
):
    """Test category_callback when cuisines are found."""
    mock_update.callback_query.data = "lunch"
    mock_get_cuisines.return_value = ["italian", "mexican"]

    with patch(
        "menu.menu_bot.conversation_helpers.inline_keyboard_generator"
    ) as mock_keyboard_gen:
        mock_keyboard_gen.return_value = [[]]
        result = await category_callback(mock_update, mock_context)

    mock_get_cuisines.assert_called_once_with("lunch")
    assert mock_context.user_data["category"] == "lunch"
    mock_update.callback_query.edit_message_text.assert_awaited_once()
    call_args = mock_update.callback_query.edit_message_text.await_args
    assert "You selected lunch" in call_args.args[0]
    assert result == ConversationStages.CUISINE.value


@patch("menu.menu_bot.conversation_helpers.get_cuisines")
async def test_category_callback_no_cuisines(
    mock_get_cuisines: MagicMock, mock_update: MagicMock, mock_context: MagicMock
):
    """Test category_callback when no cuisines are found."""
    mock_update.callback_query.data = "snack"
    mock_get_cuisines.return_value = []

    result = await category_callback(mock_update, mock_context)

    mock_get_cuisines.assert_called_once_with("snack")
    assert mock_context.user_data["category"] == "snack"
    mock_update.callback_query.edit_message_text.assert_not_called()
    assert result == ConversationStages.SUMMARY.value


@patch("menu.menu_bot.conversation_helpers.get_recipe_names")
async def test_recipe_selection_found(
    mock_get_recipes: MagicMock, mock_update: MagicMock, mock_context: MagicMock
):
    """Test recipe_selection when recipes are found."""
    mock_update.callback_query.data = "italian"
    mock_context.user_data = {"category": "lunch"}
    mock_get_recipes.return_value = [{"name": "Pizza", "id": 1}]

    with patch(
        "menu.menu_bot.conversation_helpers.inline_keyboard_generator_from_dict"
    ) as mock_keyboard_gen:
        mock_keyboard_gen.return_value = [[]]
        result = await recipe_selection(mock_update, mock_context)

    mock_get_recipes.assert_called_once_with(cuisine="italian", category="lunch")
    assert mock_context.user_data["cuisine"] == "italian"
    mock_update.callback_query.edit_message_text.assert_awaited_once()
    call_args = mock_update.callback_query.edit_message_text.await_args
    assert "I have found few recipes" in call_args.args[0]
    assert result == ConversationStages.SUMMARY.value


@patch("menu.menu_bot.conversation_helpers.get_recipe_names")
async def test_recipe_selection_not_found(
    mock_get_recipes: MagicMock, mock_update: MagicMock, mock_context: MagicMock
):
    """Test recipe_selection when no recipes are found."""
    mock_update.callback_query.data = "french"
    mock_context.user_data = {"category": "breakfast"}
    mock_get_recipes.return_value = []

    result = await recipe_selection(mock_update, mock_context)

    mock_get_recipes.assert_called_once_with(cuisine="french", category="breakfast")
    mock_update.callback_query.edit_message_text.assert_awaited_once_with(
        "Can't find anything to cook"
    )
    assert result == ConversationHandler.END
