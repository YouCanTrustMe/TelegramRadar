"""Miscellaneous bot handlers: the noop callback for inert pagination buttons
and the /logs viewer with a full-file download."""
import logging
from html import escape
from pathlib import Path

from pyrogram import filters as pf
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import settings
from src.dispatcher.sender import send_document

log = logging.getLogger(__name__)


def _tail_lines(path: Path, n: int, block_size: int = 4096) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        data = b""
        pos = size
        while pos > 0 and data.count(b"\n") <= n:
            read = min(block_size, pos)
            pos -= read
            f.seek(pos)
            data = f.read(read) + data
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def _log_file() -> Path:
    return Path(settings.database_path).parent / "logs" / "radar.log"


def register_misc_handlers(bot, admin_msg, admin_cb) -> None:

    @bot.on_callback_query(pf.regex(r"^noop$") & admin_cb)
    async def cb_noop(_, query: CallbackQuery) -> None:
        await query.answer()

    @bot.on_message(pf.command("logs") & admin_msg)
    async def cmd_logs(_, message: Message) -> None:
        log_file = _log_file()
        if not log_file.exists():
            await message.reply("No log file yet.")
            return
        tail = _tail_lines(log_file, 20)
        text = "<pre>" + escape("\n".join(tail)) + "</pre>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download full log", callback_data="logs_download")]])
        await message.reply(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^logs_download$") & admin_cb)
    async def cb_logs_download(_, query: CallbackQuery) -> None:
        log_file = _log_file()
        if not log_file.exists():
            await query.answer("Log file not found.", show_alert=True)
            return
        await query.answer()
        try:
            await send_document(query.message.chat.id, str(log_file), filename="radar.log")
        except Exception as exc:
            log.exception("Log download failed: %s", exc)
            await query.message.reply(f"Failed to send log file: {exc}")
