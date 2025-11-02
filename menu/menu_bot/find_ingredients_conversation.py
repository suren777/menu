"""This module contains the conversation handler for finding recipes by ingredients."""

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from menu.menu_bot.conversation_helpers import (
    cancel,
    search_for_recipes_by_ingredients,
    start_ingredients,
    summary,
)
from menu.menu_bot.helpers import ConversationStages

ONE, TWO = range(2)

ingredients_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("ingredients", start_ingredients),
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
