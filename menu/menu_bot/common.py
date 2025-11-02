
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


from menu.db.recipes.helpers import recipe_to_text
from menu.db.user.helpers import add_recipe_to_favorites
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays the gathered information and ends the conversation."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in summary function.")
        return ConversationHandler.END

    await query.answer()

    action = "view"
    recipe_id_str = query.data

    if query.data.startswith("save_"):
        action = "save"
        recipe_id_str = query.data.split("_")[1]

    try:
        recipe_id = int(recipe_id_str)
    except ValueError:
        logging.error("Invalid recipe_id: %s", recipe_id_str)
        await query.edit_message_text("Invalid recipe ID.")
        return ConversationHandler.END

    user = context.user_data["user"]

    if action == "save":
        if user.premium:
            add_recipe_to_favorites(user.id, recipe_id)
            await query.edit_message_text("Recipe saved to your favorites!")
        else:
            await query.edit_message_text(
                "This is a premium feature. Please upgrade to save recipes."
            )
        return ConversationHandler.END

    recipe = get_recipe_by_id(recipe_id)
    if recipe is None:
        await query.edit_message_text("Can't find anything to cook")
        return ConversationHandler.END

    user_data: dict[Any, Any] = context.user_data
    cuisine = user_data.get("cuisine")
    category = user_data.get("category")

    message = ""
    if cuisine is not None:
        message += f"Cuisine: {cuisine}\n"
    if category is not None:
        message += f"Category: {str(category).capitalize()}\n"
    message += recipe_to_text(recipe)

    keyboard = [
        [
            InlineKeyboardButton(
                "Save to favorites",
                callback_data=f"save_{recipe_id}",
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    return ConversationHandler.END


async def cancel(_: Update, __: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""

    return ConversationHandler.END
