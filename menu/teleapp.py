from telegram import Update

from menu.menu_bot.bot_config import telegram_bot
from menu.menu_bot.find_ingredients_conversation import ingredients_conversation
from menu.menu_bot.find_recipe_conversation import find_recipes_conversation
from menu.menu_bot.generate_recipe_conversation import generate_recipes_conversation


def main():
    app = telegram_bot.build()
    app.add_handler(generate_recipes_conversation)
    app.add_handler(find_recipes_conversation)
    app.add_handler(ingredients_conversation)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
