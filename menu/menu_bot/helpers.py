"""This module contains helper functions for the bot."""

from enum import Enum
from itertools import islice
from typing import Any, Iterable, Iterator

from telegram import InlineKeyboardButton

MAX_OPTIONS = 10
MAX_COLS = 1


def chunk(it: Iterable[Any], size: int) -> Iterator[tuple[Any]]:
    """Chunks an iterable into smaller iterables of a given size."""
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


class ConversationStages(Enum):
    """Enum for conversation stages."""

    MENU_TYPE = 1
    CUISINE = 2
    INGREDIENTS = 3
    SUMMARY = 4


MAIN_CATEGORIES = ["breakfast", "brunch", "lunch", "dinner", "drink"]


def keyboard_generator(
    input_array: list[Any], cols: int = MAX_COLS
) -> list[tuple[Any]]:
    """Generates a keyboard from a list of items."""
    return list(chunk(input_array, cols))


def inline_keyboard_generator(
    input_array: list[str], cols: int = MAX_COLS
) -> list[list[InlineKeyboardButton]]:
    """Generates an inline keyboard from a list of strings."""
    reply_keyboard: list[list[InlineKeyboardButton]] = []
    for ch in chunk(input_array, cols):
        reply_keyboard.append(
            [InlineKeyboardButton(b.capitalize(), callback_data=b) for b in ch]
        )
    return reply_keyboard


def inline_keyboard_generator_from_dict(
    input_array: list[dict[Any, Any]],
    name_key: str,
    data_key: str,
    cols: int = MAX_COLS,
) -> list[list[InlineKeyboardButton]]:
    """Generates an inline keyboard from a list of dictionaries."""
    reply_keyboard: list[list[InlineKeyboardButton]] = []
    for ch in chunk(input_array, cols):
        reply_keyboard.append(
            [
                InlineKeyboardButton(
                    b[name_key].capitalize(), callback_data=b[data_key]
                )
                for b in ch
            ]
        )
    return reply_keyboard
