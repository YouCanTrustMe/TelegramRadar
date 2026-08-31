"""Radar keywords: list/view/add/delete handlers plus the keyword add-flow
text input."""
import logging
from html import escape

from pyrogram import filters as pf
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.handlers.radar_common import (
    _chat_label,
    _kw_label,
    _plural,
    _radar_list_kb,
    _render_keywords,
)
from src.bot.keyboards import _back_kb, _confirm_keyboard
from src.bot.state import _pending
from src.db.radar import (
    add_radar_keyword,
    get_chats_for_keyword,
    get_radar_keywords,
    get_recent_senders_for_keyword,
    remove_radar_keyword,
)
from src.radar.matcher import (
    format_code_spec,
    infer_code_lengths,
    keyword_display,
    parse_code_spec,
)

log = logging.getLogger(__name__)

_CODE_PROMPT = (
    "🔑 <b>Code keyword</b>\n\n"
    "Drop codes have no fixed spelling, only a fixed length — <code>F3QK5</code> is 5, "
    "<code>XPMR4AZQH5</code> is 10, <code>EK6WCVEG2GKMFEJSD</code> is 17.\n\n"
    "Send either:\n"
    "• the length — <code>5</code>, <code>5,10,17</code>, or a range <code>16-18</code>\n"
    "• or just paste example codes and let it work the lengths out — "
    "<code>F3QK5 R6S9A EK6WCVEG2GKMFEJSD</code>\n\n"
    "<i>Only uppercase codes count, and each code alerts once — a repost stays quiet.</i>"
)


def _kw_view_kb(kw_id: int, has_history: bool):
    rows = []
    if has_history:
        rows.append([InlineKeyboardButton("👥 Recent senders", callback_data=f"rk_senders:{kw_id}")])
    rows.append([InlineKeyboardButton("« Back", callback_data="radar_keywords:0")])
    return InlineKeyboardMarkup(rows)


def register_keywords(bot, admin_msg, admin_cb) -> None:

    @bot.on_callback_query(pf.regex(r"^radar_keywords(:\d+)?$") & admin_cb)
    async def cb_radar_keywords(_, query: CallbackQuery) -> None:
        _pending.pop(query.from_user.id, None)
        parts = query.data.split(":")
        page = int(parts[1]) if len(parts) > 1 else 0
        text, kb = await _render_keywords(page)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^radar_kw_view:\d+$") & admin_cb)
    async def cb_radar_kw_view(_, query: CallbackQuery) -> None:
        kw_id = int(query.data.split(":")[1])
        all_kw = await get_radar_keywords()
        kw_row = next((k for k in all_kw if k["id"] == kw_id), None)
        if not kw_row:
            await query.answer("Keyword not found.", show_alert=True)
            return
        linked_chats = await get_chats_for_keyword(kw_id)
        senders = await get_recent_senders_for_keyword(kw_id)
        if linked_chats:
            chat_lines = "\n".join(f"• {escape(_chat_label(c))}" for c in linked_chats)
            text = (
                f"📋 <b>{escape(keyword_display(kw_row['keyword']))}</b>\n"
                f"<i>Watched in {_plural(len(linked_chats), 'chat')}.</i>\n\n"
                f"{chat_lines}\n\n"
                f"<i>Edit links from the chat side: 💬 Chats → tap a chat.</i>"
            )
        else:
            text = (
                f"📋 <b>{escape(keyword_display(kw_row['keyword']))}</b>\n\n"
                f"⚠️ Not linked to any chat yet.\n\n"
                f"<i>Open 💬 Chats → tap a chat → toggle this keyword on.</i>"
            )
        if senders:
            text += f"\n👥 {_plural(len(senders), 'recent sender')} tripped it."
        await query.message.edit_text(text, reply_markup=_kw_view_kb(kw_id, bool(senders)))

    @bot.on_callback_query(pf.regex(r"^radar_code_add$") & admin_cb)
    async def cb_radar_code_add(_, query: CallbackQuery) -> None:
        _pending[query.from_user.id] = {"action": "add_radar_code", "step": 0, "data": {}}
        await query.message.edit_text(_CODE_PROMPT, reply_markup=_back_kb("radar_keywords:0"))

    @bot.on_callback_query(pf.regex(r"^radar_kw_add$") & admin_cb)
    async def cb_radar_kw_add(_, query: CallbackQuery) -> None:
        uid = query.from_user.id
        _pending[uid] = {"action": "add_radar_keyword", "step": 0, "data": {}}
        await query.message.edit_text(
            "➕ <b>New keyword</b>\n\nSend the word or phrase to watch for.\n\n"
            "<i>Case and light obfuscation are handled for you.</i>",
            reply_markup=_back_kb("radar_keywords:0"),
        )

    @bot.on_callback_query(pf.regex(r"^radar_kw_del:\d+$") & admin_cb)
    async def cb_radar_kw_del(_, query: CallbackQuery) -> None:
        kw_id = int(query.data.split(":")[1])
        items = await get_radar_keywords()
        kw_row = next((k for k in items if k["id"] == kw_id), None)
        label = kw_row["keyword"] if kw_row else str(kw_id)
        await query.message.edit_text(
            f"🗑 Remove keyword <b>{escape(keyword_display(label))}</b>?\n\n"
            f"<i>Its links and sender rules go with it.</i>",
            reply_markup=_confirm_keyboard(f"radar_kw_del_ok:{kw_id}", "radar_keywords:0"),
        )

    @bot.on_callback_query(pf.regex(r"^radar_kw_del_ok:\d+$") & admin_cb)
    async def cb_radar_kw_del_ok(_, query: CallbackQuery) -> None:
        kw_id = int(query.data.split(":")[1])
        await remove_radar_keyword(kw_id)
        log.info("Radar keyword removed: id=%d", kw_id)
        text, kb = await _render_keywords(0)
        await query.message.edit_text(text, reply_markup=kb)


async def handle_keyword_input(message: Message, uid: int, text: str) -> None:
    """The ➕ Add flow. A bare length ("5", "10,17") is taken as a code keyword
    even here: it is never a word worth watching, and typing the number is what
    an admin reaches for first."""
    await _add_keyword(message, uid, text, as_code=bool(parse_code_spec(text)))


async def handle_code_input(message: Message, uid: int, text: str) -> None:
    """The 🔑 Add code flow: a length spec, or example codes to measure."""
    lengths = parse_code_spec(text)
    examples: list[int] = []
    if not lengths:
        examples = infer_code_lengths(text)
        lengths = examples
    if not lengths:
        _pending.pop(uid, None)
        await message.reply(
            f"⚠️ <b>Could not read</b> <code>{escape(text)}</code>\n\n"
            f"Send a length — <code>5</code>, <code>5,10,17</code>, <code>16-18</code> — "
            f"or paste a few example codes.",
            reply_markup=_back_kb("radar_keywords:0"),
        )
        return
    await _add_keyword(message, uid, format_code_spec(lengths), as_code=True, measured=bool(examples))


async def _add_keyword(
    message: Message, uid: int, text: str, *, as_code: bool, measured: bool = False
) -> None:
    if as_code:
        keyword = format_code_spec(parse_code_spec(text))
        kind = "code"
    else:
        keyword = text.lower()
        kind = "text"
    added = await add_radar_keyword(keyword, kind)
    _pending.pop(uid, None)
    items = await get_radar_keywords()
    kw_row = next((k for k in items if k["keyword"] == keyword), None)

    if added:
        log.info("Radar keyword added: %s (kind=%s)", keyword, kind)
        note = ""
        if kind == "code":
            lengths = parse_code_spec(keyword)
            measured_from = " measured from your examples" if measured else ""
            note = (
                f"\nWatching for uppercase codes of "
                f"{', '.join(str(n) for n in lengths)} characters{measured_from}.\n"
                f"<i>Link it to a chat under 💬 Chats to start.</i>"
            )
        header = (
            f"✅ <b>Added</b> · <code>{escape(keyword)}</code>{note}\n\n"
            f"📋 <b>Keywords</b> · {len(items)}"
        )
    else:
        header = (
            f"⚠️ <b>Already exists</b> · <code>{escape(keyword)}</code>\n\n"
            f"📋 <b>Keywords</b> · {len(items)}"
        )

    kb = _radar_list_kb(
        items, 0, "id", "radar_kw_del:", "radar_kw_add",
        "radar_keywords", _kw_label,
        view_prefix="radar_kw_view:",
        extra_add=("🔑 Add code", "radar_code_add"),
    )
    # A word that already has history is worth reviewing right away: it says who
    # is about to start pinging, without hunting through the per-chat editor.
    if kw_row and await get_recent_senders_for_keyword(kw_row["id"], 1):
        kb.inline_keyboard.insert(
            0,
            [InlineKeyboardButton(
                "👥 Who already wrote this", callback_data=f"rk_senders:{kw_row['id']}"
            )],
        )
    await message.reply(header, reply_markup=kb)
