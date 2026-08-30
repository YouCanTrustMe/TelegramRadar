import logging

from pyrogram import filters
from pyrogram.types import BotCommand, BotCommandScopeChat

from src.bot.handlers.misc import register_misc_handlers
from src.bot.handlers.radar import register_radar_bot_handlers
from src.config import settings
from src.dispatcher.sender import bot

log = logging.getLogger(__name__)

# The "/" menu is the bot's table of contents. It lived in BotFather by hand,
# which is why it still advertised two commands after the menu had grown to five
# screens; owning it here means it cannot drift from the handlers again.
_MENU = [
    ("radar", "open the radar menu"),
    ("keywords", "words and code patterns to watch"),
    ("chats", "chats being monitored"),
    ("quiet", "what the radar is holding back"),
    ("status", "health, uptime and recent alerts"),
    ("logs", "recent log entries"),
]


async def publish_command_menu() -> None:
    """Scoped to the admin's own chat: nobody else can use this bot anyway."""
    commands = [BotCommand(name, description) for name, description in _MENU]
    try:
        await bot.set_bot_commands(
            commands, scope=BotCommandScopeChat(chat_id=settings.telegram_admin_id)
        )
        log.info("Bot command menu published: %s", ", ".join(n for n, _ in _MENU))
    except Exception:
        # A stale menu is a cosmetic problem; it must never stop the radar.
        log.exception("Could not publish the bot command menu, continuing")


def register_commands() -> None:
    admin_msg = filters.user(settings.telegram_admin_id) & filters.private
    admin_cb = filters.user(settings.telegram_admin_id)
    register_misc_handlers(bot, admin_msg, admin_cb)
    register_radar_bot_handlers(bot, admin_msg, admin_cb)
