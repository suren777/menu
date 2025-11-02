"""This module contains common functions for the bot."""

import logging
from typing import Any

from telegram import (
    Update,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from menu.db.recipes.helpers import (
    get_recipe_by_id,
)


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays the gathered information and ends the conversation."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in summary function.")
        return ConversationHandler.END

    await query.answer()
    recipe_id_str = query.data
    recipe_id: int | None = None
    if recipe_id_str is not None:
        try:
            recipe_id = int(recipe_id_str)
        except ValueError:
            logging.error("Invalid recipe_id: %s", recipe_id_str)
            await query.edit_message_text("Invalid recipe ID.")
            return ConversationHandler.END

    user_data: dict[Any, Any] = context.user_data  # type: ignore
    recipe = get_recipe_by_id(recipe_id)
    cuisine = user_data.get("cuisine")
    category = user_data.get("category")

    if recipe is None:
        logging.info("Error with cuisine %s and category %s", cuisine, category)
        await query.edit_message_text("Can't find anything to cook")
        return ConversationHandler.END

    message = ""
    if cuisine is not None:
        message += f"Cuisine: {cuisine}\n"
    if category is not None:
        message += f"Category: {str(category).capitalize()}\n"
    message += recipe
    await query.edit_message_text(
        message,
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cancel(_: Update, __: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""

    return ConversationHandler.END
