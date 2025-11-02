"""This module contains helper functions for the bot conversations."""

import logging
import random
from typing import Any, cast

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from menu.db.recipes.helpers import (
    get_cuisines,
    get_recipe_by_id,
    get_recipe_names,
    search_recipe_by_name,
    search_recipes_by_ingredients,
)
from menu.menu_bot.helpers import (
    MAIN_CATEGORIES,
    MAX_OPTIONS,
    ConversationStages,
    inline_keyboard_generator,
    inline_keyboard_generator_from_dict,
)

logging.basicConfig(level=logging.INFO)

ONE, TWO = range(2)


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user about their food category."""

    reply_keyboard = inline_keyboard_generator(MAIN_CATEGORIES)

    if update.message is None:
        logging.error("Message is None in start function.")
        return ConversationHandler.END

    await update.message.reply_text(
        "<b>Welcome to the Food Recipe Bot!\n"
        "Let's get some details about the food you want to cook.\n"
        "What is the meal type?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.MENU_TYPE.value


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the category selection and asks for cuisine."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in category_callback function.")
        return ConversationHandler.END

    await query.answer()
    category = query.data
    user_data = context.user_data
    if user_data is None:
        user_data = {}
        context.user_data = user_data
    user_data["category"] = category
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
    """Handles the cuisine selection and shows the recipes."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in recipe_selection function.")
        return ConversationHandler.END

    await query.answer()
    cuisine = query.data
    user_data = context.user_data
    if user_data is None:
        user_data = {}
        context.user_data = user_data
    user_data["cuisine"] = cuisine
    category = user_data.get("category")
    recipes = get_recipe_names(cuisine=cuisine, category=category)

    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    reply_keyboard = inline_keyboard_generator_from_dict(
        cast(list[dict[Any, Any]], recipes), "name", "id"
    )
    if len(recipes) == 0:
        logging.info(
            "Error with cuisine %s and category %s",
            cuisine,
            category if category is not None else "N/A",
        )
        await query.edit_message_text("Can't find anything to cook")
        return ConversationHandler.END

    await query.edit_message_text(
        "<b>I have found few recipes, which one you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return ConversationStages.SUMMARY.value


async def search_for_recipes(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """Searches for recipes based on the user's input."""
    if update.message is None:
        logging.error("Message is None in search_for_recipes function.")
        return ConversationHandler.END

    search_string = update.message.text
    if search_string is None:
        logging.error("Search string is None in search_for_recipes function.")
        await update.message.reply_text("Please provide a search query.")
        return ConversationHandler.END

    recipes = search_recipe_by_name(search_string)
    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    if len(recipes) == 0:
        await update.message.reply_text(
            "Can't find anything to cook, try searching for something else"
        )
        return ConversationHandler.END
    reply_keyboard = inline_keyboard_generator_from_dict(
        cast(list[dict[Any, Any]], recipes), "name", "id"
    )
    await update.message.reply_text(
        "<b>I have found few recipes, which one you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return TWO


async def search_for_recipes_by_ingredients(
    update: Update, _: ContextTypes.DEFAULT_TYPE
):
    """Searches for recipes based on the user's input."""
    if update.message is None:
        logging.error("Message is None in search_for_recipes_by_ingredients function.")
        return ConversationHandler.END

    ingredients_string = update.message.text
    if ingredients_string is None:
        logging.error(
            "Ingredients string is None in search_for_recipes_by_ingredients function."
        )
        await update.message.reply_text("Please provide a list of ingredients.")
        return ConversationHandler.END

    ingredients_list = ingredients_string.split(",")
    recipes = search_recipes_by_ingredients(ingredients_list)
    if len(recipes) > MAX_OPTIONS:
        recipes = random.sample(recipes, k=MAX_OPTIONS)

    if len(recipes) == 0:
        await update.message.reply_text(
            "Can't find anything to cook with these ingredients, try searching for something else"
        )
        return ConversationHandler.END
    reply_keyboard = inline_keyboard_generator_from_dict(
        cast(list[dict[Any, Any]], recipes), "name", "id"
    )
    await update.message.reply_text(
        "<b>I have found a few recipes, which one do you want to cook?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(reply_keyboard),
    )
    return TWO


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays the gathered information and ends the conversation."""
    query = update.callback_query
    if query is None:
        logging.error("Callback query is None in summary function.")
        return ConversationHandler.END

    await query.answer()
    recipe_id_str = query.data
    recipe_id: int | None = None
    if recipe_id_str is not None:
        try:
            recipe_id = int(recipe_id_str)
        except ValueError:
            logging.error("Invalid recipe_id: %s", recipe_id_str)
            await query.edit_message_text("Invalid recipe ID.")
            return ConversationHandler.END

    user_data: dict[Any, Any] = context.user_data  # type: ignore
    recipe = get_recipe_by_id(recipe_id)
    cuisine = user_data.get("cuisine")
    category = user_data.get("category")

    if recipe is None:
        logging.info("Error with cuisine %s and category %s", cuisine, category)
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


async def cancel(_: Update, __: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""

    return ConversationHandler.END
