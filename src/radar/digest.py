"""Weekly digest of the quiet log: a summary of matches that were muted by the
sender filter over the past week, sent to the admin on a schedule.

Every group carries one full-width button into that keyword×chat filter editor,
so a group that turns out to be wrongly muted can be opened and undone from the
digest itself. The example message is linked from the group's own line: a bare
🔗 is a hard thing to hit with a thumb, so the link covers the whole phrase.
"""
import logging
from html import escape

from src.config import settings
from src.db.radar import get_muted_summary_since
from src.dispatcher.sender import send_to
from src.radar.matcher import keyword_display

log = logging.getLogger(__name__)

_DIGEST_DAYS = 7
_MAX_LINES = 20

# Only the top groups get a button; past that the keyboard is a wall and the
# quiet log is the better place to look anyway.
_MAX_BUTTON_ROWS = 8

# Telegram clips a longer inline label itself, mid-word and with no ellipsis.
_BUTTON_LABEL_MAX = 40


async def send_muted_digest() -> None:
    rows = await get_muted_summary_since(_DIGEST_DAYS)
    if not rows:
        log.info("Muted digest: nothing muted in the last %dd, skipping", _DIGEST_DAYS)
        return
    total = sum(r["cnt"] for r in rows)
    lines = []
    for r in rows[:_MAX_LINES]:
        detail = f"{r['cnt']}×"
        if r["sample_author"]:
            detail += f" · latest from {escape(r['sample_author'])}"
        if r["sample_url"]:
            detail = f'<a href="{escape(r["sample_url"])}">{detail}</a>'
        lines.append(
            f"<b>{escape(keyword_display(r['keyword']))}</b> · {escape(_chat_name(r))}\n"
            f"   ↳ {detail}"
        )
    if len(rows) > _MAX_LINES:
        lines.append(f"<i>… and {len(rows) - _MAX_LINES} more</i>")
    body = (
        f"🔇 <b>Weekly muted digest</b>\n"
        f"Last {_DIGEST_DAYS} days · <b>{total}</b> suppressed across "
        f"{len(rows)} keyword×chat {'group' if len(rows) == 1 else 'groups'}\n\n"
        + "\n\n".join(lines)
        + "\n\n<i>Tap a count to open the example · ⚙️ to edit who may alert.</i>"
    )
    keyboard = _digest_keyboard(rows)
    await send_to(
        settings.telegram_admin_id,
        body,
        disable_notification=True,
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )
    log.info(
        "Muted digest sent: total=%d groups=%d button_rows=%d",
        total, len(rows), len(keyboard),
    )


def _clip(label: str) -> str:
    """Trim at a word boundary and say so. The bot screens share this rule, but
    the digest is not a bot screen and must not import their handlers."""
    if len(label) <= _BUTTON_LABEL_MAX:
        return label
    head = label[:_BUTTON_LABEL_MAX - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head[_BUTTON_LABEL_MAX // 2:] else head
    return f"{cut.rstrip(' ·—-')}…"


def _chat_name(row) -> str:
    return row["chat_title"] or row["chat_ref"] or "unknown chat"


def _digest_keyboard(rows) -> list[list[dict]]:
    keyboard: list[list[dict]] = []
    for r in rows[:_MAX_BUTTON_ROWS]:
        label = _clip(f"{keyword_display(r['keyword'])} · {_chat_name(r)} — {r['cnt']}×")
        # Without both ids there is no editor to open — an alert logged under a
        # keyword since deleted, or a chat_ref no rename ever re-keyed. The
        # example message is still worth a button, so the row is never empty
        # and never a bare emoji.
        if r["keyword_id"] is not None and r["chat_db_id"] is not None:
            keyboard.append([{
                "text": f"⚙️ {label}",
                "callback_data": f"rf_view:{r['chat_db_id']}:{r['keyword_id']}",
            }])
        elif r["sample_url"]:
            keyboard.append([{"text": f"🔗 {label}", "url": r["sample_url"]}])
    keyboard.append([{"text": "🔇 All muted senders", "callback_data": "rms:mute:0"}])
    return keyboard
