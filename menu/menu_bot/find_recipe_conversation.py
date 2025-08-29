import logging
import random
from telegram import (
    Update,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from menu.db.recipes.helpers import (
    search_recipe_by_name,
)

from menu.menu_bot.common import summary, cancel
from menu.menu_bot.helpers import (
    ConversationStages,
    MAX_OPTIONS,
    inline_keyboard_generator_from_dict,
)

logging.basicConfig(level=logging.INFO)

ONE, TWO = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user about their food category."""

    await update.message.reply_text(
        "<b>Welcome to the Food Recipe Bot!\n"
        "What Recipe do you want to search for?</b>",
        parse_mode="HTML",
    )
    return ONE


async def search_for_recipes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_string = update.message.text
    recipes = search_recipe_by_name(search_string)
    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    if len(recipes) == 0:
        await update.message.reply_text(
            "Can't find anything to cook, try searching for something else"
        )
        return ConversationHandler.END
    reply_keyboard = inline_keyboard_generator_from_dict(recipes, "name", "id")
    await update.message.reply_text(
        f"<b>I have found few recipes, which one you want to cook?</b>",
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
