"""Weekly digest of the quiet log: a summary of matches that were muted by the
sender filter over the past week, sent to the admin on a schedule."""
import logging
from html import escape

from src.config import settings
from src.db.radar import get_muted_summary_since
from src.dispatcher.sender import send_to

log = logging.getLogger(__name__)

_DIGEST_DAYS = 7
_MAX_LINES = 30


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
            f"• <b>{escape(r['keyword'])}</b> in {escape(r['chat_ref'])} — "
            f"{r['cnt']}×{sample}"
        )
    if len(rows) > _MAX_LINES:
        lines.append(f"… and {len(rows) - _MAX_LINES} more")
    body = (
        f"🔇 <b>Weekly muted digest</b> (last {_DIGEST_DAYS}d)\n"
        f"Total suppressed: <b>{total}</b>\n\n" + "\n".join(lines)
    )
    await send_to(settings.telegram_admin_id, body, disable_notification=True)
    log.info("Muted digest sent: total=%d groups=%d", total, len(rows))
