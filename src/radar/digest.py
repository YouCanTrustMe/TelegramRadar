"""Weekly digest of the quiet log: a summary of matches that were muted by the
sender filter over the past week, sent to the admin on a schedule.

Every group carries its own buttons — a link to an example message and a jump
into that keyword×chat filter editor — so a group that turns out to be wrongly
muted can be opened and undone from the digest itself.
"""
import logging
from html import escape

from src.config import settings
from src.db.radar import get_muted_summary_since
from src.dispatcher.sender import send_to

log = logging.getLogger(__name__)

_DIGEST_DAYS = 7
_MAX_LINES = 30

# Only the top groups get a button pair; past that the keyboard is a wall and the
# quiet log is the better place to look anyway.
_MAX_BUTTON_ROWS = 8


async def send_muted_digest() -> None:
    rows = await get_muted_summary_since(_DIGEST_DAYS)
    if not rows:
        log.info("Muted digest: nothing muted in the last %dd, skipping", _DIGEST_DAYS)
        return
    total = sum(r["cnt"] for r in rows)
    lines = []
    for r in rows[:_MAX_LINES]:
        who = escape(r["sample_author"]) if r["sample_author"] else "latest"
        if r["sample_url"]:
            sample = f' (e.g. <a href="{escape(r["sample_url"])}">{who}</a>)'
        elif r["sample_author"]:
            sample = f" (e.g. {who})"
        else:
            sample = ""
        lines.append(
            f"• <b>{escape(r['keyword'])}</b> in {escape(_chat_name(r))} — "
            f"{r['cnt']}×{sample}"
        )
    if len(rows) > _MAX_LINES:
        lines.append(f"… and {len(rows) - _MAX_LINES} more")
    body = (
        f"🔇 <b>Weekly muted digest</b> (last {_DIGEST_DAYS}d)\n"
        f"Total suppressed: <b>{total}</b>\n\n" + "\n".join(lines)
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


def _chat_name(row) -> str:
    return row["chat_title"] or row["chat_ref"] or "unknown chat"


def _digest_keyboard(rows) -> list[list[dict]]:
    keyboard: list[list[dict]] = []
    for r in rows[:_MAX_BUTTON_ROWS]:
        buttons: list[dict] = []
        if r["sample_url"]:
            buttons.append({"text": "🔗", "url": r["sample_url"]})
        # Without both ids there is no editor to open — an alert logged under a
        # keyword since deleted, or a chat_ref no rename ever re-keyed.
        if r["keyword_id"] is not None and r["chat_db_id"] is not None:
            label = f"⚙️ {r['keyword']} · {_chat_name(r)}"
            buttons.append({
                "text": label[:40],
                "callback_data": f"rf_view:{r['chat_db_id']}:{r['keyword_id']}",
            })
        if buttons:
            keyboard.append(buttons)
    keyboard.append([{"text": "🔇 All muted senders", "callback_data": "rms:mute:0"}])
    return keyboard
