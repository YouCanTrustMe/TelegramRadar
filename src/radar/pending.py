"""Resend queue for alerts whose delivery was never confirmed.

A read timeout from the Bot API is ambiguous — Telegram may or may not have
accepted the message — so a resend can surface a duplicate. That is the deliberate
trade: an alert is worth more than seeing it twice. Resends carry a marker so a
duplicate is recognisable as one.
"""
import json
import logging

from src.config import settings
from src.db.radar import (
    defer_pending_alert,
    drop_pending_alert,
    enqueue_pending_alert,
    get_due_pending_alerts,
    log_radar_alert,
)
from src.dispatcher.sender import SendFailed, send_to

log = logging.getLogger(__name__)

_BACKOFF_MINUTES = (1, 2, 5, 15, 30, 60, 120, 240)
_RESEND_MARKER = "🔁 <i>resent — delivery of the original was not confirmed</i>\n"


async def queue_alert(
    message_url: str, body: str, reply_markup: dict | None, log_entries: list[dict]
) -> bool:
    return await enqueue_pending_alert(
        message_url,
        body,
        json.dumps(reply_markup) if reply_markup is not None else None,
        json.dumps(log_entries),
    )


async def _record(entries: list[dict], status: str) -> None:
    for e in entries:
        await log_radar_alert(
            e["keyword"], e["chat_ref"], e["author_id"], e["message_text"],
            e["message_url"], e["author_name"], status,
        )


async def flush_pending_alerts() -> int:
    """Returns how many queued alerts were delivered."""
    rows = await get_due_pending_alerts()
    if not rows:
        return 0
    log.info("Radar resend queue: %d alert(s) due", len(rows))
    delivered = 0
    for row in rows:
        # The body is stored apart from the payload, so an unreadable row can
        # still be delivered — only its log entry is lost.
        try:
            entries = json.loads(row["log_payload"])
            markup = json.loads(row["reply_markup"]) if row["reply_markup"] else None
        except ValueError:
            log.exception("Radar resend: unreadable queue row id=%s url=%s", row["id"], row["message_url"])
            entries, markup = [], None
        try:
            await send_to(
                settings.telegram_admin_id,
                _RESEND_MARKER + row["body"],
                reply_markup=markup,
            )
        except SendFailed as exc:
            attempts = row["attempts"] + 1
            if attempts > len(_BACKOFF_MINUTES):
                await drop_pending_alert(row["id"])
                await _record(entries, "failed")
                log.error(
                    "Radar resend gave up after %d attempts: url=%s: %s",
                    attempts, row["message_url"], exc,
                )
                continue
            delay = _BACKOFF_MINUTES[attempts - 1]
            await defer_pending_alert(row["id"], delay, str(exc))
            log.warning(
                "Radar resend failed (attempt %d, url=%s), next try in %dm: %s",
                attempts, row["message_url"], delay, exc,
            )
            break
        await drop_pending_alert(row["id"])
        await _record(entries, "sent")
        delivered += 1
        log.info(
            "Radar alert resent after %d failed attempt(s): url=%s",
            row["attempts"], row["message_url"],
        )
    return delivered
