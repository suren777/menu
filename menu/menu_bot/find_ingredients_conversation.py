import logging
import random

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from menu.db.recipes.helpers import search_recipes_by_ingredients
from menu.menu_bot.common import cancel, summary
from menu.menu_bot.helpers import (
    MAX_OPTIONS,
    ConversationStages,
    inline_keyboard_generator_from_dict,
)

logging.basicConfig(level=logging.INFO)

ONE, TWO = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user for ingredients."""

    await update.message.reply_text(
        "<b>Welcome to the Food Recipe Bot!\n"
        "Please enter a list of ingredients, separated by commas.</b>",
        parse_mode="HTML",
    )
    return ONE


async def search_for_recipes_by_ingredients(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    ingredients_string = update.message.text
    ingredients_list = ingredients_string.split(",")
    recipes = search_recipes_by_ingredients(ingredients_list)
    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    if len(recipes) == 0:
        await update.message.reply_text(
            "Can't find anything to cook with these ingredients, try searching for something else"
        )
        return ConversationHandler.END
    reply_keyboard = inline_keyboard_generator_from_dict(recipes, "name", "id")
    await update.message.reply_text(
        "<b>I have found a few recipes, which one do you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return TWO


ingredients_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("ingredients", start),
    ],
    states={
        ONE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND, search_for_recipes_by_ingredients
            ),
        ],
        TWO: [
            CallbackQueryHandler(summary),
        ],
        ConversationStages.INGREDIENTS.value: [CallbackQueryHandler(cancel)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
