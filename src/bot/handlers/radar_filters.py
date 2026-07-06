"""Sender filtering: the inline 🔇/✅ buttons attached to every alert, and the
per keyword×chat filter editor (mode + allow/mute rules). Both the alert buttons
and the editor act per keyword×chat — the alert carries one button pair per
matched keyword."""
import logging
from html import escape

from pyrogram import filters as pf
from pyrogram.enums import ChatMembersFilter
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.keyboards import _back_kb
from src.collectors.userbot import userbot
from src.db.radar import (
    add_sender_rule,
    clear_sender_rules,
    get_author_label,
    get_keyword_chat_modes,
    get_keyword_ids_for_chat,
    get_muted_alerts,
    get_radar_chats,
    get_radar_keywords,
    get_recent_trigger_senders,
    get_sender_rules_for,
    remove_sender_rule,
    set_keyword_chat_mode,
)

log = logging.getLogger(__name__)

_MUTED_VIEW_LIMIT = 20
_RECENT_SENDERS_LIMIT = 10
_RULES_PER_PAGE = 8


def _action_target(cd: str | None) -> tuple[int, int, int] | None:
    if not cd:
        return None
    parts = cd.split(":")
    if len(parts) == 4 and parts[0] in ("rmute", "ronly"):
        try:
            return int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            return None
    return None


async def _mark_row_done(
    query: CallbackQuery, kw_id: int, chat_id: int, sender_id: int, text: str
) -> None:
    """Replace only the acted keyword's button row with a confirmation, leaving the
    Open-message button and any other keyword rows tappable."""
    target = (kw_id, chat_id, sender_id)
    markup = query.message.reply_markup
    rows = [
        [InlineKeyboardButton(text, callback_data="noop")]
        if any(_action_target(b.callback_data) == target for b in row)
        else row
        for row in (markup.inline_keyboard if markup else [])
    ]
    if not rows:
        rows = [[InlineKeyboardButton(text, callback_data="noop")]]
    try:
        await query.message.edit_reply_markup(InlineKeyboardMarkup(rows))
    except Exception as exc:
        log.debug("mark_row_done: could not edit markup: %s", exc)


async def _render_muted() -> str:
    rows = await get_muted_alerts(_MUTED_VIEW_LIMIT)
    if not rows:
        return "🔇 <b>Quiet log</b>\n\nNothing muted yet."
    lines = []
    for r in rows:
        who = escape(r["author_name"]) if r["author_name"] else str(r["author_id"])
        link = f' <a href="{escape(r["message_url"])}">🔗</a>' if r["message_url"] else ""
        lines.append(
            f"• <b>{escape(r['keyword'])}</b> in {escape(r['chat_ref'])} — "
            f"{who} — {r['alerted_at'][:16]}{link}"
        )
    return f"🔇 <b>Quiet log</b> (last {len(rows)} muted)\n\n" + "\n".join(lines)


async def _find_chat(chat_id: int):
    return next((c for c in await get_radar_chats() if c["id"] == chat_id), None)


async def _find_keyword(kw_id: int):
    return next((k for k in await get_radar_keywords() if k["id"] == kw_id), None)


async def _render_filter_keywords(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat_row = await _find_chat(chat_id)
    if not chat_row:
        return "Chat not found.", _back_kb("radar_chats:0")
    linked = await get_keyword_ids_for_chat(chat_id)
    keywords = [k for k in await get_radar_keywords() if k["id"] in linked]
    title = escape(chat_row["title"] or chat_row["chat_ref"])
    buttons = [
        [InlineKeyboardButton(k["keyword"], callback_data=f"rf_view:{chat_id}:{k['id']}")]
        for k in keywords
    ]
    buttons.append([InlineKeyboardButton("◀ Back", callback_data=f"radar_chat_view:{chat_id}")])
    if keywords:
        text = f"⚙️ <b>Sender filters</b> — {title}\n\nPick a keyword to configure who alerts."
    else:
        text = f"⚙️ <b>Sender filters</b> — {title}\n\n⚠️ No keywords linked to this chat yet."
    return text, InlineKeyboardMarkup(buttons)


async def _render_filter_editor(chat_id: int, kw_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    chat_row = await _find_chat(chat_id)
    kw_row = await _find_keyword(kw_id)
    if not chat_row or not kw_row:
        return "Not found.", _back_kb("radar_chats:0")
    mode = (await get_keyword_chat_modes()).get((kw_id, chat_id), "all")
    rules = await get_sender_rules_for(kw_id, chat_id)
    chat_disp = escape(chat_row["title"] or chat_row["chat_ref"])

    total_pages = max(1, (len(rules) + _RULES_PER_PAGE - 1) // _RULES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_rules = rules[page * _RULES_PER_PAGE:(page + 1) * _RULES_PER_PAGE]

    all_mark = "✅ " if mode == "all" else ""
    allow_mark = "✅ " if mode == "allowlist" else ""
    buttons = [[
        InlineKeyboardButton(f"{all_mark}Everyone", callback_data=f"rf_mode:{chat_id}:{kw_id}:all"),
        InlineKeyboardButton(f"{allow_mark}Allowlist", callback_data=f"rf_mode:{chat_id}:{kw_id}:allowlist"),
    ]]
    for r in page_rules:
        icon = "✅" if r["action"] == "allow" else "🔇"
        who = r["label"] or str(r["sender_id"])
        buttons.append([
            InlineKeyboardButton(f"{icon} {who}", callback_data="noop"),
            InlineKeyboardButton("❌", callback_data=f"rf_del:{r['id']}:{chat_id}:{kw_id}:{page}"),
        ])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀", callback_data=f"rf_view:{chat_id}:{kw_id}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶", callback_data=f"rf_view:{chat_id}:{kw_id}:{page + 1}"))
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton("🛡 Add admins", callback_data=f"rf_admins:{chat_id}:{kw_id}"),
        InlineKeyboardButton("📋 Last 10", callback_data=f"rf_last10:{chat_id}:{kw_id}"),
    ])
    buttons.append([InlineKeyboardButton("◀ Back", callback_data=f"rf_chat:{chat_id}")])

    mode_desc = (
        "alert from <b>everyone</b> except muted senders"
        if mode == "all"
        else "alert <b>only</b> from allowed senders"
    )
    text = (
        f"⚙️ Filter: <b>{escape(kw_row['keyword'])}</b> in {chat_disp}\n"
        f"Mode: {mode_desc}.\n\n"
        f"{'Senders:' if rules else 'No sender rules yet.'}"
    )
    return text, InlineKeyboardMarkup(buttons)


async def _render_last10(chat_id: int, kw_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat_row = await _find_chat(chat_id)
    kw_row = await _find_keyword(kw_id)
    if not chat_row or not kw_row:
        return "Not found.", _back_kb("radar_chats:0")
    senders = await get_recent_trigger_senders(
        kw_row["keyword"], chat_row["chat_ref"], _RECENT_SENDERS_LIMIT
    )
    buttons = []
    for s in senders:
        who = s["author_name"] or str(s["author_id"])
        buttons.append([
            InlineKeyboardButton(f"{who} ({s['cnt']}×)", callback_data="noop"),
            InlineKeyboardButton("✅", callback_data=f"rf_add:{chat_id}:{kw_id}:{s['author_id']}:allow"),
            InlineKeyboardButton("🔇", callback_data=f"rf_add:{chat_id}:{kw_id}:{s['author_id']}:mute"),
        ])
    buttons.append([InlineKeyboardButton("◀ Back", callback_data=f"rf_view:{chat_id}:{kw_id}")])
    if senders:
        text = (
            f"📋 Recent senders of <b>{escape(kw_row['keyword'])}</b>\n\n"
            f"✅ = allow (only-list) · 🔇 = mute"
        )
    else:
        text = f"📋 No recorded senders for <b>{escape(kw_row['keyword'])}</b> yet."
    return text, InlineKeyboardMarkup(buttons)


def register_filters(bot, admin_msg, admin_cb) -> None:

    @bot.on_callback_query(pf.regex(r"^rmute:\d+:\d+:-?\d+$") & admin_cb)
    async def cb_rmute(_, query: CallbackQuery) -> None:
        _, kw_s, chat_s, sender_s = query.data.split(":")
        kw_id, chat_id, sender_id = int(kw_s), int(chat_s), int(sender_s)
        label = await get_author_label(sender_id)
        await add_sender_rule(kw_id, chat_id, sender_id, "mute", label)
        kw_row = await _find_keyword(kw_id)
        kw_name = kw_row["keyword"] if kw_row else kw_id
        log.info("Radar filter: muted sender=%s kw_id=%d chat_id=%d", sender_id, kw_id, chat_id)
        await query.answer("🔇 Muted — their matches go to the quiet log", show_alert=False)
        await _mark_row_done(query, kw_id, chat_id, sender_id, f"🔇 Muted {label or sender_id} · {kw_name} ✓")

    @bot.on_callback_query(pf.regex(r"^ronly:\d+:\d+:-?\d+$") & admin_cb)
    async def cb_ronly(_, query: CallbackQuery) -> None:
        _, kw_s, chat_s, sender_s = query.data.split(":")
        kw_id, chat_id, sender_id = int(kw_s), int(chat_s), int(sender_s)
        label = await get_author_label(sender_id)
        await set_keyword_chat_mode(kw_id, chat_id, "allowlist")
        await add_sender_rule(kw_id, chat_id, sender_id, "allow", label)
        kw_row = await _find_keyword(kw_id)
        kw_name = kw_row["keyword"] if kw_row else kw_id
        log.info("Radar filter: allowlist-only sender=%s kw_id=%d chat_id=%d", sender_id, kw_id, chat_id)
        await query.answer("✅ Now alerting only from this sender for this keyword", show_alert=True)
        await _mark_row_done(query, kw_id, chat_id, sender_id, f"✅ Only {label or sender_id} · {kw_name} ✓")

    @bot.on_callback_query(pf.regex(r"^radar_muted$") & admin_cb)
    async def cb_radar_muted(_, query: CallbackQuery) -> None:
        await query.message.edit_text(
            await _render_muted(), reply_markup=_back_kb("radar_main"), disable_web_page_preview=True
        )

    @bot.on_callback_query(pf.regex(r"^rf_chat:\d+$") & admin_cb)
    async def cb_rf_chat(_, query: CallbackQuery) -> None:
        chat_id = int(query.data.split(":")[1])
        text, kb = await _render_filter_keywords(chat_id)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_view:\d+:\d+(:\d+)?$") & admin_cb)
    async def cb_rf_view(_, query: CallbackQuery) -> None:
        parts = query.data.split(":")
        chat_id, kw_id = int(parts[1]), int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
        text, kb = await _render_filter_editor(chat_id, kw_id, page)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_mode:\d+:\d+:(all|allowlist)$") & admin_cb)
    async def cb_rf_mode(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s, mode = query.data.split(":")
        chat_id, kw_id = int(chat_s), int(kw_s)
        await set_keyword_chat_mode(kw_id, chat_id, mode)
        if mode == "all":
            n = await clear_sender_rules(kw_id, chat_id)
            log.info("Radar filter: mode -> all, cleared %d rule(s) kw_id=%d chat_id=%d", n, kw_id, chat_id)
        else:
            log.info("Radar filter: mode -> allowlist kw_id=%d chat_id=%d", kw_id, chat_id)
        text, kb = await _render_filter_editor(chat_id, kw_id)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_del:\d+:\d+:\d+:\d+$") & admin_cb)
    async def cb_rf_del(_, query: CallbackQuery) -> None:
        _, rule_s, chat_s, kw_s, page_s = query.data.split(":")
        await remove_sender_rule(int(rule_s))
        log.info("Radar filter: rule removed id=%s", rule_s)
        text, kb = await _render_filter_editor(int(chat_s), int(kw_s), int(page_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_add:\d+:\d+:-?\d+:(allow|mute)$") & admin_cb)
    async def cb_rf_add(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s, sender_s, action = query.data.split(":")
        label = await get_author_label(int(sender_s))
        await add_sender_rule(int(kw_s), int(chat_s), int(sender_s), action, label)
        log.info("Radar filter: rule add kw_id=%s chat_id=%s sender=%s action=%s", kw_s, chat_s, sender_s, action)
        await query.answer(f"{'✅ allowed' if action == 'allow' else '🔇 muted'}")
        text, kb = await _render_last10(int(chat_s), int(kw_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_last10:\d+:\d+$") & admin_cb)
    async def cb_rf_last10(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s = query.data.split(":")
        text, kb = await _render_last10(int(chat_s), int(kw_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_admins:\d+:\d+$") & admin_cb)
    async def cb_rf_admins(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s = query.data.split(":")
        chat_id, kw_id = int(chat_s), int(kw_s)
        chat_row = await _find_chat(chat_id)
        if not chat_row:
            await query.answer("Chat not found", show_alert=True)
            return
        probe = chat_row["chat_id"] if chat_row["chat_id"] is not None else chat_row["chat_ref"]
        added = 0
        try:
            async for m in userbot.get_chat_members(probe, filter=ChatMembersFilter.ADMINISTRATORS):
                u = m.user
                if not u or u.is_bot:
                    continue
                name = f"{u.first_name or ''} {u.last_name or ''}".strip() or (
                    f"@{u.username}" if u.username else str(u.id)
                )
                await add_sender_rule(kw_id, chat_id, u.id, "allow", name)
                added += 1
        except Exception as exc:
            log.warning("Radar filter: add admins failed chat_id=%d: %s", chat_id, exc)
            await query.answer(f"Could not fetch admins: {exc}", show_alert=True)
            return
        await set_keyword_chat_mode(kw_id, chat_id, "allowlist")
        log.info("Radar filter: added %d admin(s) to allowlist kw_id=%d chat_id=%d", added, kw_id, chat_id)
        await query.answer(f"🛡 Added {added} admin(s) to allowlist", show_alert=True)
        text, kb = await _render_filter_editor(chat_id, kw_id)
        await query.message.edit_text(text, reply_markup=kb)
