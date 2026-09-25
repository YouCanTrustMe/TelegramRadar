import asyncio
import logging

import aiosqlite

from src.collectors.userbot import userbot
from src.db.base import get_db
from src.db.radar import (
    get_all_sender_rules,
    get_keyword_chat_links,
    get_keyword_chat_modes,
    get_radar_chats,
    get_radar_keywords,
    update_radar_last_message_at,
)
from src.radar.handlers import process_radar_message
from src.radar.pending import flush_pending_alerts

log = logging.getLogger(__name__)

POLL_INTERVAL = 60


async def _set_last_seen(entry_id: int, msg_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_chats SET last_seen_msg_id = ? WHERE id = ? "
            "AND (last_seen_msg_id IS NULL OR last_seen_msg_id < ?)",
            (msg_id, entry_id, msg_id),
        )
        await db.commit()


# One failed read heals itself — last_seen does not move, so the next poll picks
# up what this one missed — and Telegram returns the odd 500 RPC_CALL_FAIL. Only
# a chat that keeps failing is worth a warning.
_WARN_AFTER_FAILURES = 2
_failures: dict[int, int] = {}


async def _fetch_new(row: aiosqlite.Row) -> list:
    """Messages newer than the chat's last_seen, oldest first."""
    keys = row.keys()
    chat_id = row["chat_id"] if "chat_id" in keys else None
    if chat_id is None:
        return []
    last_seen = row["last_seen_msg_id"] if "last_seen_msg_id" in keys else None
    limit = 5 if last_seen is None else 50

    new_messages = []
    try:
        async for msg in userbot.get_chat_history(chat_id, limit=limit):
            if last_seen is not None and msg.id <= last_seen:
                break
            new_messages.append(msg)
    except Exception as exc:
        failures = _failures.get(chat_id, 0) + 1
        _failures[chat_id] = failures
        log.log(
            logging.WARNING if failures == _WARN_AFTER_FAILURES else logging.INFO,
            "Radar poll failed for chat %s (%d in a row): %s", chat_id, failures, exc,
        )
        return []
    failures = _failures.pop(chat_id, 0)
    if failures:
        log.info("Radar poll recovered for chat %s after %d failure(s)", chat_id, failures)

    if last_seen is None:
        # First sight of a chat: start from its newest message instead of
        # alerting on history.
        if new_messages:
            await _set_last_seen(row["id"], max(m.id for m in new_messages))
            await update_radar_last_message_at(row["id"])
        return []
    if new_messages:
        log.debug("Radar: %d new message(s) in chat %s", len(new_messages), chat_id)
    return list(reversed(new_messages))


async def _poll_cycle() -> None:
    chats = await get_radar_chats()
    keywords = await get_radar_keywords()
    links_by_chat: dict[int, set[int]] = {}
    for link in await get_keyword_chat_links():
        links_by_chat.setdefault(link["chat_id"], set()).add(link["keyword_id"])
    modes = await get_keyword_chat_modes()
    rules = await get_all_sender_rules()

    batches = []
    for row in chats:
        if "status" in row.keys() and row["status"] != "active":
            continue
        batches.append((row, await _fetch_new(row)))

    # A code reposted from the original chat into another lands in the same
    # cycle; processing chat by chat let whichever chat was polled first claim
    # it. In the order it was posted, the original wins.
    queue = [(msg, row) for row, msgs in batches for msg in msgs]
    queue.sort(key=lambda item: item[0].date)
    total_alerts = 0
    for msg, row in queue:
        try:
            if await process_radar_message(
                msg,
                row,
                keywords=keywords,
                linked_kw_ids=links_by_chat.get(row["id"], set()),
                modes=modes,
                rules=rules,
            ):
                total_alerts += 1
        except Exception:
            log.exception("Radar processing failed for chat=%s msg=%s", row["chat_id"], msg.id)

    for row, msgs in batches:
        if msgs:
            await _set_last_seen(row["id"], max(m.id for m in msgs))
            await update_radar_last_message_at(row["id"])
    if total_alerts:
        log.info("Radar poll cycle: %d alert(s) sent", total_alerts)


async def run_radar_collector() -> None:
    log.info("Radar collector started (interval=%ds)", POLL_INTERVAL)
    while True:
        try:
            # The resend queue is best-effort; monitoring must never wait on it.
            try:
                await flush_pending_alerts()
            except Exception:
                log.exception("Radar resend queue failed, continuing with the poll")
            await _poll_cycle()
        except Exception:
            log.exception("Radar collector iteration failed")
        await asyncio.sleep(POLL_INTERVAL)
