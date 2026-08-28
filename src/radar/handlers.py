import logging
from datetime import timezone
from html import escape
from zoneinfo import ZoneInfo

from src.config import settings
from src.db.radar import filter_unseen_codes, log_radar_alert, record_seen_codes
from src.dispatcher.sender import SendFailed, send_to
from src.radar.matcher import find_codes, keyword_display, match_keywords, parse_code_spec
from src.radar.pending import queue_alert

log = logging.getLogger(__name__)


# A message full of code-shaped tokens is spam, not a drop: cap what one message
# can contribute so the dedup lookup can never build an unbounded query.
_MAX_CODES_PER_MESSAGE = 40


def _kind(row) -> str:
    """Keyword rows predate the `kind` column on databases mid-migration."""
    return row["kind"] if "kind" in row.keys() and row["kind"] else "text"


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

    linked = [row for row in keywords if row["id"] in linked_kw_ids]
    if not linked:
        log.debug("Radar: msg=%s skipped — no keywords linked to chat db_id=%s", message.id, chat_row["id"])
        return False
    code_rows = [row for row in linked if _kind(row) == "code"]
    text_keywords = [row["keyword"] for row in linked if _kind(row) != "code"]
    matched = match_keywords(
        text,
        text_keywords,
        leet=settings.radar_match_leet,
        fuzzy=settings.radar_match_fuzzy,
        translit=settings.radar_match_translit,
        merge_min_len=settings.radar_match_merge_min_len,
    )

    chat_id = message.chat.id
    if message.chat.username:
        msg_link = f"https://t.me/{message.chat.username}/{message.id}"
        chat_ref_str = f"@{message.chat.username}"
    else:
        pure_id = abs(chat_id) - 1000000000000
        msg_link = f"https://t.me/c/{pure_id}/{message.id}"
        chat_ref_str = str(chat_id)

    # Code keywords match the shape of a drop code rather than a word, and a code
    # is single-use: every sighting is remembered, and one already seen inside the
    # dedup window is counted but never alerted again.
    code_hits: dict[str, list[str]] = {}
    all_codes: list[str] = []
    for row in code_rows:
        found = find_codes(text, parse_code_spec(row["keyword"]))[:_MAX_CODES_PER_MESSAGE]
        if found:
            code_hits[row["keyword"]] = found
            all_codes.extend(found)
    if all_codes:
        all_codes = list(dict.fromkeys(all_codes))[:_MAX_CODES_PER_MESSAGE]
        unseen = await filter_unseen_codes(all_codes, settings.radar_code_dedup_days)
        await record_seen_codes(all_codes, chat_ref_str, msg_link)
        for kw, found in code_hits.items():
            fresh = [c for c in found if c in unseen]
            if fresh:
                code_hits[kw] = fresh
                matched.append(kw)
            else:
                log.info(
                    "Radar: codes already seen, not alerting keyword=%s codes=%s chat=%s msg=%s",
                    kw, found, chat_ref_str, message.id,
                )
        code_hits = {kw: found for kw, found in code_hits.items() if kw in matched}

    if not matched:
        log.debug(
            "Radar: msg=%s skipped — no keyword match (checked: %s + %d code pattern(s))",
            message.id, text_keywords, len(code_rows),
        )
        return False

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
        btn_sender = f"@{author.username}" if author.username else (author.first_name or str(author_id))
    elif sender_chat:
        author_name = sender_chat.title or None
        btn_sender = f"@{sender_chat.username}" if sender_chat.username else (sender_chat.title or str(author_id))
    else:
        author_name = None
        btn_sender = str(sender_id)
    btn_sender = btn_sender[:20]

    # Per keyword×chat sender filter: 'all' alerts unless the sender is muted,
    # 'allowlist' alerts only from explicitly allowed senders. Suppressed matches
    # go to the quiet log (status='muted') instead of pinging.
    chat_db_id = chat_row["id"]
    kw_id_by_text = {row["keyword"]: row["id"] for row in linked}
    passing: list[str] = []
    suppressed: list[str] = []
    for kw in matched:
        kw_id = kw_id_by_text.get(kw)
        mode = modes.get((kw_id, chat_db_id), "all")
        action = rules.get((kw_id, chat_db_id, sender_id))
        allowed = (action == "allow") if mode == "allowlist" else (action != "mute")
        (passing if allowed else suppressed).append(kw)

    for kw in suppressed:
        await log_radar_alert(
            kw, chat_ref_str, author_id, text, msg_link, author_name, "muted", chat_db_id
        )
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

    passing_words = [kw for kw in passing if kw not in code_hits]
    passing_codes: list[str] = []
    for kw in passing:
        for code in code_hits.get(kw, []):
            if code not in passing_codes:
                passing_codes.append(code)

    header = ""
    if passing_words:
        kw_label = "Keyword" if len(passing_words) == 1 else "Keywords"
        kw_str = ", ".join(escape(kw) for kw in passing_words)
        header += f"🔍 {kw_label}:\n<blockquote>{kw_str}</blockquote>\n"
    if passing_codes:
        code_label = "Code" if len(passing_codes) == 1 else "Codes"
        # <code> renders tap-to-copy in Telegram, which is the whole point of
        # catching these: the code is meant to be pasted, not read.
        codes_str = "\n".join(f"<code>{escape(c)}</code>" for c in passing_codes)
        header += f"🔑 {code_label}:\n<blockquote>{codes_str}</blockquote>\n"

    alert_body = (
        f"{header}"
        f"💬 Chat: {chat_disp}\n"
        f"👤 From: {from_str}\n"
        f"⏱️ {ts}\n"
        f"<blockquote expandable>{escape(short_text)}</blockquote>"
    )
    keyboard = [[{"text": "🔗 Open message", "url": msg_link}]]
    if sender_id is not None:
        for kw in passing:
            kw_id = kw_id_by_text.get(kw)
            if kw_id is None:
                continue
            label = keyword_display(kw)
            keyboard.append([
                {"text": f"🔇 {btn_sender} · {label}", "callback_data": f"rmute:{kw_id}:{chat_db_id}:{sender_id}"},
                {"text": f"✅👤 {btn_sender} · {label}", "callback_data": f"ronly:{kw_id}:{chat_db_id}:{sender_id}"},
            ])
    reply_markup = {"inline_keyboard": keyboard}
    try:
        await send_to(settings.telegram_admin_id, alert_body, reply_markup=reply_markup)
    except SendFailed as exc:
        log_entries = [
            {
                "keyword": kw, "chat_ref": chat_ref_str, "author_id": author_id,
                "message_text": text, "message_url": msg_link, "author_name": author_name,
                "chat_db_id": chat_db_id,
            }
            for kw in passing
        ]
        queued = await queue_alert(msg_link, alert_body, reply_markup, log_entries)
        log.warning(
            "Radar alert not confirmed, %s: keywords=%s chat=%s author_id=%s msg=%s url=%s: %s",
            "queued for resend" if queued else "already queued",
            passing, chat_title, author_id, message.id, msg_link, exc,
        )
        return False
    for kw in passing:
        await log_radar_alert(
            kw, chat_ref_str, author_id, text, msg_link, author_name, "sent", chat_db_id
        )
    log.info(
        "Radar alert sent: keywords=%s chat=%s author_id=%s",
        passing,
        chat_title,
        author_id,
    )
    return True
