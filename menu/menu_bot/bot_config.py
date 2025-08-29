from telegram.ext import Application
from menu.const import BOT_KEY

telegram_bot = Application.builder().token(BOT_KEY)
