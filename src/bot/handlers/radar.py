"""Radar entrypoint: wires the keyword/chat/filter submodules, the main menu,
the status screen and the single shared add-flow conversation handler."""
import logging
from datetime import datetime, timezone
from html import escape

from pyrogram import filters as pf
from pyrogram.types import CallbackQuery, Message

from src.bot.handlers.radar_chats import handle_chat_input, register_chats
from src.bot.handlers.radar_common import (
    _radar_main_kb,
    _render_chats,
    _render_keywords,
    _short_ts,
)
from src.bot.handlers.radar_filters import register_filters, render_quiet_hub
from src.bot.handlers.radar_keywords import (
    handle_code_input,
    handle_keyword_input,
    register_keywords,
)
from src.bot.keyboards import _back_kb
from src.bot.state import _pending
from src.db.radar import (
    count_pending_alerts,
    count_repeat_codes,
    get_radar_chats,
    get_radar_keywords,
    get_recent_radar_alerts,
    get_silent_radar_chats,
)
from src.radar.matcher import keyword_display

_SILENT_THRESHOLD_HOURS = 120
_STATUS_MARK = {"muted": " 🔇", "failed": " ⚠️"}

log = logging.getLogger(__name__)

_start_time = datetime.now(timezone.utc)

_MAIN_TEXT = (
    "🔍 <b>Radar</b>\n"
    "<i>Real-time keyword alerts from the chats you watch.</i>"
)

_RADAR_INPUT_HANDLERS = {
    "add_radar_keyword": handle_keyword_input,
    "add_radar_chat": handle_chat_input,
    "add_radar_code": handle_code_input,
}


async def _render_status() -> str:
    chats = await get_radar_chats()
    keywords = await get_radar_keywords()
    alerts = await get_recent_radar_alerts(3)

    delta = datetime.now(timezone.utc) - _start_time
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes = rem // 60

    alert_lines = ""
    if alerts:
        alert_lines = "\n\n🔔 <b>Last alerts</b>\n" + "\n".join(
            f"• <b>{escape(keyword_display(r['keyword']))}</b> · "
            f"{escape(r['chat_ref'])} — {_short_ts(r['alerted_at'])}"
            f"{_STATUS_MARK.get(r['status'], '')}"
            for r in alerts
        )

    pending = await count_pending_alerts()
    pending_line = f"\n⚠️ Awaiting resend: <b>{pending}</b>" if pending else ""

    repeats = await count_repeat_codes()
    codes_line = f"\n🔑 Repeat codes silenced: <b>{repeats}</b>" if repeats else ""

    quiet_lines = ""
    silent = await get_silent_radar_chats(_SILENT_THRESHOLD_HOURS)
    if silent:
        quiet_lines = (
            f"\n\n💤 <b>Quiet chats</b> <i>(over {_SILENT_THRESHOLD_HOURS}h)</i>\n"
            + "\n".join(
                f"• {escape(r['title'] or r['chat_ref'])} — {r['hours_silent']}h"
                for r in silent
            )
        )

    text = (
        f"📊 <b>Radar status</b>\n\n"
        f"💬 Chats monitored: <b>{len(chats)}</b>\n"
        f"📋 Keywords active: <b>{len(keywords)}</b>\n"
        f"⏱ Uptime: <b>{hours}h {minutes}m</b>"
        f"{pending_line}"
        f"{codes_line}"
        f"{alert_lines}"
        f"{quiet_lines}"
    )
    return text


def register_radar_bot_handlers(bot, admin_msg, admin_cb) -> None:
    register_keywords(bot, admin_msg, admin_cb)
    register_chats(bot, admin_msg, admin_cb)
    register_filters(bot, admin_msg, admin_cb)

    @bot.on_message((pf.command("radar") | pf.command("start")) & admin_msg)
    async def cmd_radar(_, message: Message) -> None:
        await message.reply(_MAIN_TEXT, reply_markup=await _radar_main_kb())

    @bot.on_callback_query(pf.regex(r"^radar_main$") & admin_cb)
    async def cb_radar_main(_, query: CallbackQuery) -> None:
        _pending.pop(query.from_user.id, None)
        await query.message.edit_text(_MAIN_TEXT, reply_markup=await _radar_main_kb())

    @bot.on_callback_query(pf.regex(r"^radar_status$") & admin_cb)
    async def cb_radar_status(_, query: CallbackQuery) -> None:
        await query.message.edit_text(await _render_status(), reply_markup=_back_kb("radar_main"))

    # Each command opens a screen that already exists — the "/" menu is the bot's
    # table of contents, not a second way to do things.
    @bot.on_message(pf.command("keywords") & admin_msg)
    async def cmd_keywords(_, message: Message) -> None:
        _pending.pop(message.from_user.id, None)
        text, kb = await _render_keywords(0)
        await message.reply(text, reply_markup=kb)

    @bot.on_message(pf.command("chats") & admin_msg)
    async def cmd_chats(_, message: Message) -> None:
        _pending.pop(message.from_user.id, None)
        text, kb = await _render_chats(0)
        await message.reply(text, reply_markup=kb)

    @bot.on_message(pf.command("quiet") & admin_msg)
    async def cmd_quiet(_, message: Message) -> None:
        _pending.pop(message.from_user.id, None)
        text, kb = await render_quiet_hub()
        await message.reply(text, reply_markup=kb)

    @bot.on_message(pf.command("status") & admin_msg)
    async def cmd_status(_, message: Message) -> None:
        _pending.pop(message.from_user.id, None)
        await message.reply(await _render_status(), reply_markup=_back_kb("radar_main"))


    @bot.on_message(pf.private & admin_msg, group=1)
    async def handle_radar_conversation(_, message: Message) -> None:
        if not message.text or message.text.startswith("/"):
            return
        uid = message.from_user.id
        state = _pending.get(uid)
        if not state or not state.get("action", "").startswith("add_radar"):
            return
        handler = _RADAR_INPUT_HANDLERS.get(state["action"])
        if handler:
            await handler(message, uid, message.text.strip())
