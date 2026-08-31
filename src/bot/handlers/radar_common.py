"""Shared presentation helpers for the radar handlers: the main menu keyboard,
the generic paginated list keyboard, row labels and the keyword/chat list
renderers reused across the radar_* modules."""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.db.radar import (
    get_keyword_chat_links,
    get_muted_alerts_count,
    get_radar_chats,
    get_radar_keywords,
)
from src.radar.matcher import keyword_display

_PAGE_SIZE = 10


# Telegram clips a longer inline label itself, mid-word and with no ellipsis.
_LABEL_MAX = 40


def _clip(label: str) -> str:
    """Trim a button label at a word boundary and say so, rather than letting
    Telegram cut "Key-Drop.com Chat" into "Key-Drop.com Ch"."""
    if len(label) <= _LABEL_MAX:
        return label
    head = label[:_LABEL_MAX - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head[_LABEL_MAX // 2:] else head
    return f"{cut.rstrip(' ·-')}…"


def _plural(n: int, word: str, suffix: str = "s") -> str:
    """"1 rule" / "2 rules" — the "(s)" shorthand is fine in a log line and shabby
    on a screen the admin reads every day."""
    return f"{n} {word}" if n == 1 else f"{n} {word}{suffix}"


def _short_ts(alerted_at: str) -> str:
    """"2026-08-31 09:00:12" -> "31-08 09:00". The year is noise in a list that
    only ever shows the last few days."""
    date, _, time = alerted_at.partition(" ")
    parts = date.split("-")
    if len(parts) == 3 and time:
        return f"{parts[2]}-{parts[1]} {time[:5]}"
    return alerted_at[:16]


async def _radar_main_kb() -> InlineKeyboardMarkup:
    """The counts ride on the buttons: the menu is the one screen seen on every
    visit, and "12 keywords across 4 chats" is most of what a check-in asks."""
    kw = len(await get_radar_keywords())
    chats = len(await get_radar_chats())
    quiet = await get_muted_alerts_count()
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"📋 Keywords · {kw}", callback_data="radar_keywords:0"),
            InlineKeyboardButton(f"💬 Chats · {chats}", callback_data="radar_chats:0"),
        ],
        [
            InlineKeyboardButton(
                f"🔇 Quiet · {quiet}" if quiet else "🔇 Quiet", callback_data="radar_quiet"
            ),
            InlineKeyboardButton("📊 Status", callback_data="radar_status"),
        ],
    ])


def _radar_list_kb(
    items,
    page: int,
    id_field: str,
    del_prefix: str,
    add_cb: str,
    list_cb_base: str,
    label_fn,
    view_prefix: str | None = None,
    extra_add: tuple[str, str] | None = None,
) -> InlineKeyboardMarkup:
    total = len(items)
    start = page * _PAGE_SIZE
    page_items = items[start : start + _PAGE_SIZE]
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    buttons = []
    for row in page_items:
        label_cb = f"{view_prefix}{row[id_field]}" if view_prefix else "noop"
        buttons.append([
            InlineKeyboardButton(_clip(label_fn(row)), callback_data=label_cb),
            InlineKeyboardButton("❌", callback_data=f"{del_prefix}{row[id_field]}"),
        ])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("‹", callback_data=f"{list_cb_base}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("›", callback_data=f"{list_cb_base}:{page + 1}"))
        buttons.append(nav)

    add_row = [InlineKeyboardButton("➕ Add", callback_data=add_cb)]
    if extra_add:
        add_row.append(InlineKeyboardButton(extra_add[0], callback_data=extra_add[1]))
    buttons.append(add_row)
    buttons.append([InlineKeyboardButton("« Back", callback_data="radar_main")])
    return InlineKeyboardMarkup(buttons)


def _chat_label(row, kw_count: int | None = None) -> str:
    status = row["status"] if "status" in row.keys() else "active"
    prefix = "⚠️ " if status != "active" else ""
    base = f"{row['title']} ({row['chat_ref']})" if row["title"] else row["chat_ref"]
    suffix = ""
    if kw_count is not None:
        suffix = " — ⚠️ unbound" if kw_count == 0 else f" · {kw_count}kw"
    return f"{prefix}{base}{suffix}"


def _kw_label(row, chat_count: int | None = None) -> str:
    keys = row.keys() if hasattr(row, "keys") else ()
    is_code = "kind" in keys and row["kind"] == "code"
    # keyword_display already carries the 🔑; the raw "code:5" spec is machinery.
    base = keyword_display(row["keyword"]) if is_code else row["keyword"]
    if chat_count is None:
        return base
    return f"⚠️ {base} — unbound" if chat_count == 0 else f"{base} · {chat_count}ch"


async def _render_keywords(page: int) -> tuple[str, InlineKeyboardMarkup]:
    items = await get_radar_keywords()
    links = await get_keyword_chat_links()
    kw_counts: dict[int, int] = {}
    for link in links:
        kw_counts[link["keyword_id"]] = kw_counts.get(link["keyword_id"], 0) + 1
    if items:
        text = (
            f"📋 <b>Keywords</b> · {len(items)}\n\n"
            f"<i>Tap a word to see where it fires · ❌ removes it.</i>"
        )
    else:
        text = (
            "📋 <b>Keywords</b>\n\nNothing watched yet.\n\n"
            "<i>➕ adds a word · 🔑 adds a drop-code pattern.</i>"
        )
    kb = _radar_list_kb(
        items, page, "id", "radar_kw_del:", "radar_kw_add",
        "radar_keywords",
        lambda r: _kw_label(r, kw_counts.get(r["id"], 0)),
        view_prefix="radar_kw_view:",
        extra_add=("🔑 Add code", "radar_code_add"),
    )
    return text, kb


async def _render_chats(page: int) -> tuple[str, InlineKeyboardMarkup]:
    items = await get_radar_chats()
    links = await get_keyword_chat_links()
    chat_counts: dict[int, int] = {}
    for link in links:
        chat_counts[link["chat_id"]] = chat_counts.get(link["chat_id"], 0) + 1
    if items:
        text = (
            f"💬 <b>Chats</b> · {len(items)}\n\n"
            f"<i>Tap a chat to link keywords · ❌ removes it and leaves.</i>"
        )
    else:
        text = (
            "💬 <b>Chats</b>\n\nNothing monitored yet.\n\n"
            "<i>➕ adds a chat by @username, id or invite link.</i>"
        )
    kb = _radar_list_kb(
        items, page, "id", "radar_chat_del:", "radar_chat_add",
        "radar_chats",
        lambda r: _chat_label(r, chat_counts.get(r["id"], 0)),
        view_prefix="radar_chat_view:",
    )
    return text, kb
