"""This module contains the conversation handler for the quick recipe feature."""
import logging
import random

from typing import Any, cast
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from menu.db.recipes.helpers import get_cuisines, get_recipe_names
from menu.menu_bot.common import cancel, summary
from menu.menu_bot.helpers import (
    MAIN_CATEGORIES,
    MAX_OPTIONS,
    ConversationStages,
    inline_keyboard_generator,
    inline_keyboard_generator_from_dict,
)

logging.basicConfig(level=logging.INFO)


async def start_15(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user about their food category."""

    reply_keyboard = inline_keyboard_generator(MAIN_CATEGORIES)

    if update.message is None:
        logging.error("Message is None in start_15 function.")
        return ConversationHandler.END

    await update.message.reply_text(
        "<b>Welcome to the Food Recipe Bot!\n"
        "I understand that you are in a hurry, so let's find something to cook.\n"
        "What is the meal type?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.MENU_TYPE.value


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the category selection and asks for cuisine."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in category_callback function.")
        return ConversationHandler.END

    await query.answer()
    category = query.data
    user_data = context.user_data
    if user_data is None:
        user_data = {}
        context.user_data = user_data
    user_data["category"] = category
    cuisines = get_cuisines(category)
    if len(cuisines) == 0:
        return ConversationStages.SUMMARY.value
    if len(cuisines) > MAX_OPTIONS:
        cuisines = random.sample(cuisines, k=MAX_OPTIONS)

    reply_keyboard = inline_keyboard_generator(cuisines)

    await query.edit_message_text(
        f"<b>You selected {category}.\n" f"What cuisine you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.CUISINE.value


async def recipe_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the cuisine selection and shows the recipes."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in recipe_selection function.")
        return ConversationHandler.END

    await query.answer()
    cuisine = query.data
    user_data = context.user_data
    if user_data is None:
        user_data = {}
        context.user_data = user_data
    user_data["cuisine"] = cuisine
    category = user_data.get("category")
    recipes = get_recipe_names(cuisine=cuisine, category=category)

    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    reply_keyboard = inline_keyboard_generator_from_dict(
        cast(list[dict[Any, Any]], recipes), "name", "id"
    )
    if len(recipes) == 0:
        logging.info(
            "Error with cuisine %s and category %s",
            cuisine,
            category if category is not None else "N/A",
        )
        await query.edit_message_text("Can't find anything to cook")
        return ConversationHandler.END

    await query.edit_message_text(
        "<b>I have found few recipes, which one you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.SUMMARY.value


quick_recipe_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("quick_15", start_15),
    ],
    states={
        ConversationStages.MENU_TYPE.value: [
            CallbackQueryHandler(category_callback),
        ],
        ConversationStages.CUISINE.value: [
            CallbackQueryHandler(recipe_selection),
        ],
        ConversationStages.SUMMARY.value: [
            CallbackQueryHandler(summary),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
