import logging
from datetime import timezone
from html import escape
from zoneinfo import ZoneInfo

from src.config import settings
from src.db.radar import log_radar_alert
from src.dispatcher.sender import send_to
from src.radar.matcher import match_keywords

log = logging.getLogger(__name__)


async def process_radar_message(
    message,
    chat_row,
    *,
    keywords: list,
    linked_kw_ids: set[int],
    modes: dict[tuple[int, int], str] | None = None,
    rules: dict[tuple[int, int, int], str] | None = None,
) -> bool:
    modes = modes or {}
    rules = rules or {}
    text = str(message.text or message.caption or "")
    sender_chat = getattr(message, "sender_chat", None)
    from_user = message.from_user
    auto_fwd = getattr(message, "is_automatic_forward", None)
    fwd_chat = getattr(message, "forward_from_chat", None)
    log.debug(
        "Radar: msg=%s chat=%s from_user=%s sender_chat=%s auto_forward=%s fwd_from_chat=%s text_len=%d text=%r",
        message.id,
        message.chat.id,
        from_user.id if from_user else None,
        sender_chat.id if sender_chat else None,
        auto_fwd,
        fwd_chat.id if fwd_chat else None,
        len(text),
        text[:120],
    )
    if not text:
        log.debug("Radar: msg=%s skipped — no text/caption", message.id)
        return False

    chat_keywords = [row["keyword"] for row in keywords if row["id"] in linked_kw_ids]
    if not chat_keywords:
        log.debug("Radar: msg=%s skipped — no keywords linked to chat db_id=%s", message.id, chat_row["id"])
        return False
    matched = match_keywords(text, chat_keywords)
    if not matched:
        log.debug("Radar: msg=%s skipped — no keyword match (checked: %s)", message.id, chat_keywords)
        return False

    chat_id = message.chat.id
    if message.chat.username:
        msg_link = f"https://t.me/{message.chat.username}/{message.id}"
        chat_ref_str = f"@{message.chat.username}"
    else:
        pure_id = abs(chat_id) - 1000000000000
        msg_link = f"https://t.me/c/{pure_id}/{message.id}"
        chat_ref_str = str(chat_id)

    chat_title = message.chat.title or chat_ref_str
    author = message.from_user
    if author:
        name = f"{author.first_name or ''} {author.last_name or ''}".strip() or "—"
        username = f"@{author.username}" if author.username else "—"
        author_id = author.id
        from_str = (
            f'<a href="tg://user?id={author_id}"><b>{escape(name)}</b></a> '
            f"({escape(username)}) · id: <code>{author_id}</code>"
        )
    elif sender_chat:
        title = sender_chat.title or ""
        uname = f"@{sender_chat.username}" if sender_chat.username else "—"
        author_id = sender_chat.id
        from_str = f"📢 <b>{escape(title)}</b> ({escape(uname)}) · id: <code>{author_id}</code>"
    else:
        author_id = None
        from_str = "—"

    sender_id = author_id
    if author:
        author_name = name
    elif sender_chat:
        author_name = sender_chat.title or None
    else:
        author_name = None

    # Per keyword×chat sender filter: 'all' alerts unless the sender is muted,
    # 'allowlist' alerts only from explicitly allowed senders. Suppressed matches
    # go to the quiet log (status='muted') instead of pinging.
    chat_db_id = chat_row["id"]
    kw_id_by_text = {row["keyword"]: row["id"] for row in keywords if row["id"] in linked_kw_ids}
    passing: list[str] = []
    suppressed: list[str] = []
    for kw in matched:
        kw_id = kw_id_by_text.get(kw)
        mode = modes.get((kw_id, chat_db_id), "all")
        action = rules.get((kw_id, chat_db_id, sender_id))
        allowed = (action == "allow") if mode == "allowlist" else (action != "mute")
        (passing if allowed else suppressed).append(kw)

    for kw in suppressed:
        await log_radar_alert(kw, chat_ref_str, author_id, text, msg_link, author_name, "muted")
    if suppressed:
        log.info(
            "Radar: muted keywords=%s author_id=%s chat=%s (filtered to quiet log)",
            suppressed, sender_id, chat_title,
        )
    if not passing:
        return False

    if message.date:
        dt = message.date
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.astimezone(ZoneInfo(settings.radar_timezone)).strftime("%d-%m %H:%M")
    else:
        ts = "—"
    short_text = text[:500] + ("..." if len(text) > 500 else "")

    if message.chat.username and message.chat.title:
        chat_disp = f"<b>{escape(chat_title)}</b> (@{escape(message.chat.username)})"
    elif message.chat.username:
        chat_disp = f"<b>@{escape(message.chat.username)}</b>"
    else:
        chat_disp = f"<b>{escape(chat_title)}</b>"

    kw_label = "Keyword" if len(passing) == 1 else "Keywords"
    kw_str = ", ".join(escape(kw) for kw in passing)
    alert_body = (
        f"🔍 {kw_label}:\n"
        f"<blockquote>{kw_str}</blockquote>\n"
        f"💬 Chat: {chat_disp}\n"
        f"👤 From: {from_str}\n"
        f'🔗 <a href="{escape(msg_link, quote=True)}">Open message</a> · ⏱️ {ts}\n'
        f"<blockquote expandable>{escape(short_text)}</blockquote>"
    )
    reply_markup = None
    if sender_id is not None:
        reply_markup = {
            "inline_keyboard": [[
                {"text": "🔇 Mute sender", "callback_data": f"rmute:{chat_db_id}:{sender_id}"},
                {"text": "✅ Only this", "callback_data": f"ronly:{chat_db_id}:{sender_id}"},
            ]]
        }
    await send_to(settings.telegram_admin_id, alert_body, reply_markup=reply_markup)
    for kw in passing:
        await log_radar_alert(kw, chat_ref_str, author_id, text, msg_link, author_name, "sent")
    log.info(
        "Radar alert sent: keywords=%s chat=%s author_id=%s",
        passing,
        chat_title,
        author_id,
    )
    return True
