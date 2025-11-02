"""This module contains the conversation handler for finding recipes by name."""

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from menu.menu_bot.conversation_helpers import (
    cancel,
    search_for_recipes,
    start_search_recipes,
    summary,
)
from menu.menu_bot.helpers import ConversationStages

ONE, TWO = range(2)

find_recipes_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("search", start_search_recipes),
    ],
    states={
        ONE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, search_for_recipes),
        ],
        TWO: [
            CallbackQueryHandler(summary),
        ],
        ConversationStages.INGREDIENTS.value: [CallbackQueryHandler(cancel)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
