"""This module contains the bot configuration."""

from telegram.ext import ApplicationBuilder

from menu.const import BOT_KEY

telegram_bot = ApplicationBuilder().token(BOT_KEY)
