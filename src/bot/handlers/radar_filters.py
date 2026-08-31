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
    block_code,
    clear_sender_rules,
    get_author_label,
    get_blocked_codes,
    get_muted_alerts_count,
    get_keyword_chat_modes,
    get_keyword_ids_for_chat,
    get_muted_alerts,
    get_radar_chats,
    get_radar_keywords,
    get_recent_senders_for_keyword,
    get_recent_trigger_senders,
    get_rules_for_sender,
    get_sender_rule_summary,
    get_sender_rules_for,
    remove_sender_rule,
    remove_sender_rules_for,
    reset_empty_allowlists,
    set_keyword_chat_mode,
    unblock_code,
)
from src.bot.handlers.radar_common import _clip, _plural, _short_ts
from src.radar.matcher import keyword_display

log = logging.getLogger(__name__)

_MUTED_VIEW_LIMIT = 20
_RECENT_SENDERS_LIMIT = 10
_KEYWORD_SENDERS_LIMIT = 15
_RULES_PER_PAGE = 8
_SENDERS_PER_PAGE = 8
_BLOCKED_CODES_LIMIT = 30

_ACTION_TITLE = {"mute": ("🔇", "Muted senders"), "allow": ("✅", "Allowed senders")}


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


async def _replace_button(query: CallbackQuery, callback_data: str, text: str) -> None:
    """Swap one button for a confirmation, leaving the rest of the alert live."""
    markup = query.message.reply_markup
    rows = [
        [InlineKeyboardButton(text, callback_data="noop")]
        if any(b.callback_data == callback_data for b in row)
        else row
        for row in (markup.inline_keyboard if markup else [])
    ]
    try:
        await query.message.edit_reply_markup(InlineKeyboardMarkup(rows))
    except Exception as exc:
        log.debug("replace_button: could not edit markup: %s", exc)


async def _render_muted() -> str:
    rows = await get_muted_alerts(_MUTED_VIEW_LIMIT)
    if not rows:
        return (
            "📋 <b>Suppressed matches</b>\n\nNothing muted yet.\n\n"
            "<i>Matches hidden by a sender filter land here instead of pinging you.</i>"
        )
    lines = []
    for r in rows:
        who = escape(r["author_name"]) if r["author_name"] else str(r["author_id"])
        # The whole keyword×chat phrase is the link, not a trailing 🔗: a one-emoji
        # tap target is a miss waiting to happen on a phone.
        head = (
            f"<b>{escape(keyword_display(r['keyword']))}</b> · "
            f"{escape(r['chat_ref'])}"
        )
        if r["message_url"]:
            head = f'<a href="{escape(r["message_url"])}">{head}</a>'
        lines.append(f"{head}\n   ↳ {who} · {_short_ts(r['alerted_at'])}")
    return (
        f"📋 <b>Suppressed matches</b> · last {len(rows)}\n\n"
        + "\n\n".join(lines)
        + "\n\n<i>Tap a line to open the message it was hidden from.</i>"
    )


async def render_quiet_hub() -> tuple[str, InlineKeyboardMarkup]:
    """Suppressed matches, muted senders and blocked codes are three answers to
    one question — what is the radar not telling me — so they share a screen."""
    muted = await get_muted_alerts_count()
    senders = len(await get_sender_rule_summary("mute"))
    codes = len(await get_blocked_codes())
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📋 Suppressed matches · {muted}", callback_data="radar_muted")],
        [InlineKeyboardButton(f"🔇 Muted senders · {senders}", callback_data="rms:mute:0")],
        [InlineKeyboardButton(f"🚫 Blocked codes · {codes}", callback_data="rcblocked")],
        [InlineKeyboardButton("« Back", callback_data="radar_main")],
    ])
    text = (
        "🔇 <b>Quiet</b>\n"
        "<i>What the radar is holding back.</i>\n\n"
        "📋 <b>Suppressed matches</b> — hidden by a sender filter\n"
        "🔇 <b>Muted senders</b> — silenced for a keyword\n"
        "🚫 <b>Blocked codes</b> — tokens that only looked like a code"
    )
    return text, kb


async def _render_blocked_codes() -> tuple[str, InlineKeyboardMarkup]:
    rows = await get_blocked_codes()
    buttons = [
        [
            InlineKeyboardButton(f"🔑 {r['code']}", callback_data="noop"),
            InlineKeyboardButton("❌", callback_data=f"rcunblk:{r['code']}"),
        ]
        for r in rows[:_BLOCKED_CODES_LIMIT]
    ]
    buttons.append([InlineKeyboardButton("« Back", callback_data="radar_quiet")])
    if rows:
        text = (
            f"🚫 <b>Blocked codes</b> · {len(rows)}\n\n"
            f"Tokens that look like a code but never were — they never alert again.\n\n"
            f"<i>❌ removes the block.</i>"
        )
    else:
        text = (
            "🚫 <b>Blocked codes</b>\n\nNothing blocked.\n\n"
            "<i>Use “🚫 Not a code” on a code alert when the radar catches "
            "something that only looks like one.</i>"
        )
    return text, InlineKeyboardMarkup(buttons)


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
        [InlineKeyboardButton(
            f"⚙️ {keyword_display(k['keyword'])}", callback_data=f"rf_view:{chat_id}:{k['id']}"
        )]
        for k in keywords
    ]
    buttons.append([InlineKeyboardButton("« Back", callback_data=f"radar_chat_view:{chat_id}")])
    if keywords:
        text = (
            f"⚙️ <b>Sender filters</b>\n💬 {title}\n\n"
            f"<i>Pick a keyword to choose who may alert with it here.</i>"
        )
    else:
        text = (
            f"⚙️ <b>Sender filters</b>\n💬 {title}\n\n"
            f"⚠️ No keywords linked to this chat yet."
        )
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

    # A radio pair reads as a choice; a lone ✅ on the active one reads as a
    # button that does something.
    buttons = [[
        InlineKeyboardButton(
            f"{'🔘' if mode == 'all' else '⚪'} Everyone",
            callback_data=f"rf_mode:{chat_id}:{kw_id}:all",
        ),
        InlineKeyboardButton(
            f"{'🔘' if mode == 'allowlist' else '⚪'} Allowlist",
            callback_data=f"rf_mode:{chat_id}:{kw_id}:allowlist",
        ),
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
            nav.append(InlineKeyboardButton("‹", callback_data=f"rf_view:{chat_id}:{kw_id}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("›", callback_data=f"rf_view:{chat_id}:{kw_id}:{page + 1}"))
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton("🛡 Add admins", callback_data=f"rf_admins:{chat_id}:{kw_id}"),
        InlineKeyboardButton("📋 Last 10", callback_data=f"rf_last10:{chat_id}:{kw_id}"),
    ])
    buttons.append([InlineKeyboardButton("« Back", callback_data=f"rf_chat:{chat_id}")])

    mode_desc = (
        "alert from <b>everyone</b> except muted senders"
        if mode == "all"
        else "alert <b>only</b> from allowed senders"
    )
    warning = ""
    if mode == "allowlist" and not any(r["action"] == "allow" for r in rules):
        warning = (
            "\n⚠️ <b>Nobody is on the allowlist — this keyword alerts on nothing.</b>\n"
            "Add a sender below, or switch back to Everyone."
        )
    text = (
        f"⚙️ <b>{escape(keyword_display(kw_row['keyword']))}</b>\n"
        f"💬 {chat_disp}\n\n"
        f"Mode: {mode_desc}.{warning}\n\n"
        f"{'<b>Sender rules</b>' if rules else '<i>No sender rules yet.</i>'}"
    )
    return text, InlineKeyboardMarkup(buttons)


async def _render_last10(chat_id: int, kw_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat_row = await _find_chat(chat_id)
    kw_row = await _find_keyword(kw_id)
    if not chat_row or not kw_row:
        return "Not found.", _back_kb("radar_chats:0")
    senders = await get_recent_trigger_senders(
        kw_row["keyword"], chat_id, _RECENT_SENDERS_LIMIT
    )
    kw_disp = escape(keyword_display(kw_row["keyword"]))
    chat_disp = escape(chat_row["title"] or chat_row["chat_ref"])
    buttons = []
    for s in senders:
        who = s["author_name"] or str(s["author_id"])
        label = _clip(f"👤 {who} · {s['cnt']}×")
        # The name opens their last message, as it does on the keyword-wide
        # senders screen: deciding whether to mute someone means reading what
        # they actually wrote.
        buttons.append([
            InlineKeyboardButton(label, url=s["last_url"])
            if s["last_url"]
            else InlineKeyboardButton(label, callback_data="noop"),
            InlineKeyboardButton("✅", callback_data=f"rf_add:{chat_id}:{kw_id}:{s['author_id']}:allow"),
            InlineKeyboardButton("🔇", callback_data=f"rf_add:{chat_id}:{kw_id}:{s['author_id']}:mute"),
        ])
    buttons.append([InlineKeyboardButton("« Back", callback_data=f"rf_view:{chat_id}:{kw_id}")])
    if senders:
        text = (
            f"👥 <b>Recent senders</b> · {kw_disp}\n💬 {chat_disp}\n\n"
            f"<i>Tap a name to open their message · ✅ alert only from them · "
            f"🔇 mute them for this keyword.</i>"
        )
    else:
        text = (
            f"👥 <b>Recent senders</b> · {kw_disp}\n💬 {chat_disp}\n\n"
            f"Nobody has tripped it here yet."
        )
    return text, InlineKeyboardMarkup(buttons)


async def _render_keyword_senders(kw_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Who has recently tripped this keyword, across every chat it watches.

    The per-chat editor answers the same question one chat at a time; asked at
    the keyword itself, it is the list you want when deciding whether a new word
    is going to be worth its notifications."""
    kw_row = await _find_keyword(kw_id)
    if not kw_row:
        return "Keyword not found.", _back_kb("radar_keywords:0")
    senders = await get_recent_senders_for_keyword(kw_id, _KEYWORD_SENDERS_LIMIT)
    buttons = []
    for sdr in senders:
        who = sdr["author_name"] or str(sdr["author_id"])
        where = sdr["chat_title"] or sdr["chat_ref"] or "?"
        state = {"mute": "🔇 ", "allow": "✅ "}.get(sdr["action"], "")
        label = _clip(f"{state}{who} · {where} · {sdr['cnt']}×")
        row = [
            InlineKeyboardButton(label, url=sdr["last_url"])
            if sdr["last_url"]
            else InlineKeyboardButton(label, callback_data="noop")
        ]
        target = f"{sdr['chat_db_id']}:{kw_id}:{sdr['author_id']}"
        if sdr["action"] != "mute":
            row.append(InlineKeyboardButton("🔇", callback_data=f"rk_add:{target}:mute"))
        if sdr["action"] != "allow":
            row.append(InlineKeyboardButton("✅", callback_data=f"rk_add:{target}:allow"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("« Back", callback_data=f"radar_kw_view:{kw_id}")])
    kw_disp = escape(keyword_display(kw_row["keyword"]))
    if senders:
        text = (
            f"👥 <b>Recent senders</b> · {kw_disp}\n"
            f"<i>Across every chat this keyword watches — last {len(senders)}.</i>\n\n"
            f"<i>Tap a name to open their message · 🔇 mute · ✅ alert only from them.</i>"
        )
    else:
        text = (
            f"👥 <b>Recent senders</b> · {kw_disp}\n\nNobody has tripped it yet."
        )
    return text, InlineKeyboardMarkup(buttons)


async def _render_sender_list(action: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Every sender carrying rules of one action, across all keywords and chats."""
    icon, title = _ACTION_TITLE[action]
    rows = await get_sender_rule_summary(action)
    total_pages = max(1, (len(rows) + _SENDERS_PER_PAGE - 1) // _SENDERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_rows = rows[page * _SENDERS_PER_PAGE:(page + 1) * _SENDERS_PER_PAGE]

    buttons = []
    for r in page_rows:
        who = r["label"] or str(r["sender_id"])
        buttons.append([
            InlineKeyboardButton(
                _clip(f"{icon} {who} · {_plural(r['cnt'], 'rule')}"),
                callback_data=f"rms_view:{r['sender_id']}",
            ),
            InlineKeyboardButton("❌", callback_data=f"rms_clear:{r['sender_id']}:{action}:{page}"),
        ])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("‹", callback_data=f"rms:{action}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("›", callback_data=f"rms:{action}:{page + 1}"))
        buttons.append(nav)
    other = "allow" if action == "mute" else "mute"
    other_icon, other_title = _ACTION_TITLE[other]
    buttons.append([InlineKeyboardButton(f"{other_icon} {other_title}", callback_data=f"rms:{other}:0")])
    buttons.append([InlineKeyboardButton("« Back", callback_data="radar_quiet")])

    if rows:
        text = (
            f"{icon} <b>{title}</b> · {_plural(len(rows), 'sender')} · "
            f"{_plural(sum(r['cnt'] for r in rows), 'rule')}\n\n"
            f"<i>Tap a name to see and edit their rules · ❌ clears every "
            f"{'mute' if action == 'mute' else 'allow'} for them at once.</i>"
        )
    else:
        text = f"{icon} <b>{title}</b>\n\nNothing here yet."
    return text, InlineKeyboardMarkup(buttons)


async def _render_sender_detail(sender_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rules = await get_rules_for_sender(sender_id)
    if not rules:
        return "No rules left for this sender.", _back_kb("rms:mute:0")
    who = next((r["label"] for r in rules if r["label"]), None) or str(sender_id)
    buttons = []
    for r in rules:
        icon = "✅" if r["action"] == "allow" else "🔇"
        where = r["chat_title"] or r["chat_ref"]
        buttons.append([
            InlineKeyboardButton(
                _clip(f"{icon} {keyword_display(r['keyword'])} · {where}"), callback_data="noop"
            ),
            InlineKeyboardButton("❌", callback_data=f"rms_del:{r['id']}:{sender_id}"),
        ])
    buttons.append([InlineKeyboardButton("« Back", callback_data="rms:mute:0")])
    text = (
        f"👤 <b>{escape(who)}</b>\n"
        f"id <code>{sender_id}</code> · {_plural(len(rules), 'rule')}\n\n"
        f"<i>❌ removes one rule.</i>"
    )
    return text, InlineKeyboardMarkup(buttons)


def register_filters(bot, admin_msg, admin_cb) -> None:

    @bot.on_callback_query(pf.regex(r"^rmute:\d+:\d+:-?\d+$") & admin_cb)
    async def cb_rmute(_, query: CallbackQuery) -> None:
        _, kw_s, chat_s, sender_s = query.data.split(":")
        kw_id, chat_id, sender_id = int(kw_s), int(chat_s), int(sender_s)
        label = await get_author_label(sender_id)
        await add_sender_rule(kw_id, chat_id, sender_id, "mute", label)
        # Muting overwrites any allow rule this sender had; that can be the last
        # name on the allowlist, which would leave the keyword alerting on nothing.
        reopened = await reset_empty_allowlists([(kw_id, chat_id)])
        kw_row = await _find_keyword(kw_id)
        kw_name = keyword_display(kw_row["keyword"]) if kw_row else kw_id
        log.info(
            "Radar filter: muted sender=%s kw_id=%d chat_id=%d, allowlists reopened=%d",
            sender_id, kw_id, chat_id, reopened,
        )
        await query.answer(
            "🔇 Muted — allowlist was emptied, back to everyone" if reopened
            else "🔇 Muted — their matches go to the quiet log",
            show_alert=bool(reopened),
        )
        await _mark_row_done(query, kw_id, chat_id, sender_id, f"🔇 {label or sender_id} · {kw_name} ✓")

    @bot.on_callback_query(pf.regex(r"^ronly:\d+:\d+:-?\d+$") & admin_cb)
    async def cb_ronly(_, query: CallbackQuery) -> None:
        _, kw_s, chat_s, sender_s = query.data.split(":")
        kw_id, chat_id, sender_id = int(kw_s), int(chat_s), int(sender_s)
        label = await get_author_label(sender_id)
        await set_keyword_chat_mode(kw_id, chat_id, "allowlist")
        await add_sender_rule(kw_id, chat_id, sender_id, "allow", label)
        kw_row = await _find_keyword(kw_id)
        kw_name = keyword_display(kw_row["keyword"]) if kw_row else kw_id
        log.info("Radar filter: allowlist-only sender=%s kw_id=%d chat_id=%d", sender_id, kw_id, chat_id)
        await query.answer("✅ Now alerting only from this sender for this keyword", show_alert=True)
        await _mark_row_done(query, kw_id, chat_id, sender_id, f"✅👤 {label or sender_id} · {kw_name} ✓")

    @bot.on_callback_query(pf.regex(r"^radar_muted$") & admin_cb)
    async def cb_radar_muted(_, query: CallbackQuery) -> None:
        await query.message.edit_text(
            await _render_muted(),
            reply_markup=_back_kb("radar_quiet"),
            disable_web_page_preview=True,
        )

    @bot.on_callback_query(pf.regex(r"^radar_quiet$") & admin_cb)
    async def cb_radar_quiet(_, query: CallbackQuery) -> None:
        text, kb = await render_quiet_hub()
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rcblocked$") & admin_cb)
    async def cb_rcblocked(_, query: CallbackQuery) -> None:
        text, kb = await _render_blocked_codes()
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rcblk:[0-9A-Z]{3,40}$") & admin_cb)
    async def cb_rcblk(_, query: CallbackQuery) -> None:
        code = query.data.split(":", 1)[1]
        await block_code(code)
        log.info("Radar codes: blocked code=%s", code)
        await query.answer(f"🚫 {code} will never alert again")
        await _replace_button(query, query.data, f"🚫 {code} ✓")

    @bot.on_callback_query(pf.regex(r"^rcunblk:[0-9A-Z]{3,40}$") & admin_cb)
    async def cb_rcunblk(_, query: CallbackQuery) -> None:
        code = query.data.split(":", 1)[1]
        removed = await unblock_code(code)
        log.info("Radar codes: unblocked code=%s removed=%s", code, removed)
        await query.answer(f"{code} unblocked" if removed else "Not blocked")
        text, kb = await _render_blocked_codes()
        await query.message.edit_text(text, reply_markup=kb)

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
        emptied = await remove_sender_rule(int(rule_s))
        reopened = await reset_empty_allowlists([emptied] if emptied else [])
        log.info("Radar filter: rule removed id=%s, allowlists reopened=%d", rule_s, reopened)
        text, kb = await _render_filter_editor(int(chat_s), int(kw_s), int(page_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_add:\d+:\d+:-?\d+:(allow|mute)$") & admin_cb)
    async def cb_rf_add(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s, sender_s, action = query.data.split(":")
        kw_id, chat_id = int(kw_s), int(chat_s)
        label = await get_author_label(int(sender_s))
        await add_sender_rule(kw_id, chat_id, int(sender_s), action, label)
        reopened = 0 if action == "allow" else await reset_empty_allowlists([(kw_id, chat_id)])
        log.info(
            "Radar filter: rule add kw_id=%s chat_id=%s sender=%s action=%s allowlists_reopened=%d",
            kw_s, chat_s, sender_s, action, reopened,
        )
        await query.answer(
            "✅ allowed" if action == "allow"
            else ("🔇 muted — allowlist was emptied, back to everyone" if reopened else "🔇 muted")
        )
        text, kb = await _render_last10(int(chat_s), int(kw_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rf_last10:\d+:\d+$") & admin_cb)
    async def cb_rf_last10(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s = query.data.split(":")
        text, kb = await _render_last10(int(chat_s), int(kw_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rk_senders:\d+$") & admin_cb)
    async def cb_rk_senders(_, query: CallbackQuery) -> None:
        kw_id = int(query.data.split(":")[1])
        text, kb = await _render_keyword_senders(kw_id)
        await query.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

    @bot.on_callback_query(pf.regex(r"^rk_add:\d+:\d+:-?\d+:(allow|mute)$") & admin_cb)
    async def cb_rk_add(_, query: CallbackQuery) -> None:
        _, chat_s, kw_s, sender_s, action = query.data.split(":")
        kw_id, chat_id, sender_id = int(kw_s), int(chat_s), int(sender_s)
        label = await get_author_label(sender_id)
        if action == "allow":
            await set_keyword_chat_mode(kw_id, chat_id, "allowlist")
        await add_sender_rule(kw_id, chat_id, sender_id, action, label)
        reopened = 0 if action == "allow" else await reset_empty_allowlists([(kw_id, chat_id)])
        log.info(
            "Radar filter: rule add from keyword view kw_id=%d chat_id=%d sender=%d "
            "action=%s allowlists_reopened=%d",
            kw_id, chat_id, sender_id, action, reopened,
        )
        await query.answer(
            "✅ allowed — only they alert now" if action == "allow"
            else ("🔇 muted — allowlist was emptied, back to everyone" if reopened else "🔇 muted"),
            show_alert=bool(reopened),
        )
        text, kb = await _render_keyword_senders(kw_id)
        await query.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

    @bot.on_callback_query(pf.regex(r"^rms:(allow|mute):\d+$") & admin_cb)
    async def cb_rms(_, query: CallbackQuery) -> None:
        _, action, page_s = query.data.split(":")
        text, kb = await _render_sender_list(action, int(page_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rms_view:-?\d+$") & admin_cb)
    async def cb_rms_view(_, query: CallbackQuery) -> None:
        text, kb = await _render_sender_detail(int(query.data.split(":")[1]))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rms_del:\d+:-?\d+$") & admin_cb)
    async def cb_rms_del(_, query: CallbackQuery) -> None:
        _, rule_s, sender_s = query.data.split(":")
        emptied = await remove_sender_rule(int(rule_s))
        reopened = await reset_empty_allowlists([emptied] if emptied else [])
        log.info(
            "Radar filter: rule removed id=%s sender=%s, allowlists reopened=%d",
            rule_s, sender_s, reopened,
        )
        text, kb = await _render_sender_detail(int(sender_s))
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(pf.regex(r"^rms_clear:-?\d+:(allow|mute):\d+$") & admin_cb)
    async def cb_rms_clear(_, query: CallbackQuery) -> None:
        _, sender_s, action, page_s = query.data.split(":")
        removed, emptied = await remove_sender_rules_for(int(sender_s), action)
        reopened = await reset_empty_allowlists(emptied)
        log.info(
            "Radar filter: cleared %d %s rule(s) for sender=%s, allowlists reopened=%d",
            removed, action, sender_s, reopened,
        )
        note = f", {_plural(reopened, 'keyword')} back to everyone" if reopened else ""
        await query.answer(f"Removed {_plural(removed, 'rule')}{note}")
        text, kb = await _render_sender_list(action, int(page_s))
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
        await query.answer(f"🛡 Added {_plural(added, 'admin')} to the allowlist", show_alert=True)
        text, kb = await _render_filter_editor(chat_id, kw_id)
        await query.message.edit_text(text, reply_markup=kb)
