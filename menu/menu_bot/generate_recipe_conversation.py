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
)

from menu.db.recipes.helpers import (
    get_cuisines,
    get_recipe_names,
)

from menu.menu_bot.common import summary, cancel
from menu.menu_bot.helpers import (
    MAIN_CATEGORIES,
    ConversationStages,
    inline_keyboard_generator,
    MAX_OPTIONS,
    inline_keyboard_generator_from_dict,
)

logging.basicConfig(level=logging.INFO)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user about their food category."""

    reply_keyboard = inline_keyboard_generator(MAIN_CATEGORIES)

    await update.message.reply_text(
        "<b>Welcome to the Food Recipe Bot!\n"
        "Let's get some details about the food you want to cook.\n"
        "What is the meal type?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.MENU_TYPE.value


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data
    context.user_data["category"] = category
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
    query = update.callback_query
    await query.answer()
    cuisine = query.data
    context.user_data["cuisine"] = cuisine
    category = context.user_data.get("category")
    recipes = get_recipe_names(cuisine=cuisine, category=category)

    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    reply_keyboard = inline_keyboard_generator_from_dict(recipes, "name", "id")
    if len(recipes) == 0:
        logging.info(f"Error with cuisine {cuisine} and category {category}")
        await query.edit_message_text("Can't find anything to cook")
        return ConversationHandler.END

    await query.edit_message_text(
        f"<b>I have found few recipes, which one you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.SUMMARY.value


generate_recipes_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("generate", start),
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
        ConversationStages.INGREDIENTS.value: [CallbackQueryHandler(cancel)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
