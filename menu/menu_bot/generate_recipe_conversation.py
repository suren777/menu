"""This module contains the conversation handler for generating recipes."""

from menu.menu_bot.conversation_helpers import (
    create_conversation_handler,
    start,
)

generate_recipes_conversation = create_conversation_handler("generate", start)
