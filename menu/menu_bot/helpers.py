from enum import Enum
from typing import Any, Iterator
from telegram import InlineKeyboardButton


from itertools import islice


MAX_OPTIONS = 10
MAX_COLS = 1


def chunk(it: list[Any], size: int) -> Iterator[tuple[Any]]:
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


class ConversationStages(Enum):
    MENU_TYPE = 1
    CUISINE = 2
    INGREDIENTS = 3
    SUMMARY = 4


MAIN_CATEGORIES = ["breakfast", "brunch", "lunch", "dinner", "drink"]


def keyboard_generator(
    input_array: list[Any], cols: int = MAX_COLS
) -> list[tuple[Any]]:
    return list(chunk(input_array, cols))


def inline_keyboard_generator(
    input_array: list[str], cols: int = MAX_COLS
) -> list[list[InlineKeyboardButton]]:
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
