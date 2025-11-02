"""This module contains the conversation handler for viewing favorite recipes."""
import logging
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)
from menu.db.user.helpers import get_or_create_user, get_favorite_recipes
from menu.db.recipes.helpers import recipe_to_text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and displays the user's favorite recipes."""
    if update.message is None:
        logging.error("Message is None in start function.")
        return ConversationHandler.END

    if update.message.from_user is None:
        logging.error("User is None in start function.")
        return ConversationHandler.END

    user = get_or_create_user(update.message.from_user.id)
    context.user_data["user"] = user

    if user.premium:
        favorites = get_favorite_recipes(user.id)
        if favorites:
            message = "<b>Your favorite recipes:</b>\n\n"
            for recipe in favorites:
                message += recipe_to_text(recipe) + "\n\n"
        else:
            message = "You have no favorite recipes yet."
    else:
        message = "This is a premium feature. Please upgrade to view your favorite recipes."

    await update.message.reply_text(message, parse_mode="HTML")
    return ConversationHandler.END


favorites_conversation = ConversationHandler(
    entry_points=[CommandHandler("favorites", start)],
    states={},
    fallbacks=[],
)
