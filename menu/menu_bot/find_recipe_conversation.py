"""This module contains the conversation handler for finding recipes by name."""

import logging
import random
from typing import Any, cast

from telegram import (
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from menu.db.recipes.helpers import (
    search_recipe_by_name,
)
from menu.menu_bot.common import cancel, summary
from menu.menu_bot.helpers import (
    MAX_OPTIONS,
    ConversationStages,
    inline_keyboard_generator_from_dict,
)

logging.basicConfig(level=logging.INFO)

ONE, TWO = range(2)


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user for a recipe to search for."""
    if update.message is None:
        logging.error("Message is None in start function.")
        return ConversationHandler.END

    await update.message.reply_text(
        "<b>Welcome to the Food Recipe Bot!\n"
        "What Recipe do you want to search for?</b>",
        parse_mode="HTML",
    )
    return ONE


async def search_for_recipes(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """Searches for recipes based on the user's input."""
    if update.message is None:
        logging.error("Message is None in search_for_recipes function.")
        return ConversationHandler.END

    search_string = update.message.text
    if search_string is None:
        logging.error("Search string is None in search_for_recipes function.")
        await update.message.reply_text("Please provide a search query.")
        return ConversationHandler.END

    recipes = search_recipe_by_name(search_string)
    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    if len(recipes) == 0:
        await update.message.reply_text(
            "Can't find anything to cook, try searching for something else"
        )
        return ConversationHandler.END
    reply_keyboard = inline_keyboard_generator_from_dict(
        cast(list[dict[Any, Any]], recipes), "name", "id"
    )
    await update.message.reply_text(
        "<b>I have found few recipes, which one you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return TWO


find_recipes_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("search", start),
    ],
    states={
        ONE: [
            MessageHandler(filters.TEXT, search_for_recipes),
        ],
        TWO: [
            CallbackQueryHandler(summary),
        ],
        ConversationStages.INGREDIENTS.value: [CallbackQueryHandler(cancel)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
