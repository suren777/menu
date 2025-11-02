"""This module contains the conversation handler for the quick recipe feature."""


from menu.menu_bot.conversation_helpers import (
    create_conversation_handler,
    start_quick_recipe,
)

quick_recipe_conversation = create_conversation_handler(
    "quick_15", start_quick_recipe
)
