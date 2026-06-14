from pyrogram import filters

from src.bot.handlers.misc import register_misc_handlers
from src.bot.handlers.radar import register_radar_bot_handlers
from src.config import settings
from src.dispatcher.sender import bot


def register_commands() -> None:
    admin_msg = filters.user(settings.telegram_admin_id) & filters.private
    admin_cb = filters.user(settings.telegram_admin_id)
    register_misc_handlers(bot, admin_msg, admin_cb)
    register_radar_bot_handlers(bot, admin_msg, admin_cb)
