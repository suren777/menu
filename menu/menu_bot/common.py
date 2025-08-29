import logging
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
    await query.answer()
    recipe_id = query.data
    recipe = get_recipe_by_id(recipe_id)
    cuisine = context.user_data.get("cuisine")
    category = context.user_data.get("category")

    if recipe is None:
        logging.info(f"Error with cuisine {cuisine} and category {category}")
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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""

    return ConversationHandler.END
