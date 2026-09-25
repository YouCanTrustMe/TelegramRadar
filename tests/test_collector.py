"""The poll cycle: messages from every chat are handled in the order they were
posted, and a chat that fails once is not worth a warning."""
import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.collectors import radar_collector
from src.config import settings
from src.db.base import get_db, init_db
from src.db.radar import add_radar_chat, get_radar_chats
from src.radar import handlers


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def never_reach_telegram(monkeypatch):
    async def guard(*args, **kwargs):
        raise AssertionError("test attempted a live Bot API call")

    monkeypatch.setattr("src.dispatcher.sender.send_to", guard)
    monkeypatch.setattr(handlers, "send_to", guard)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "radar.db"))
    run(init_db())
    monkeypatch.setattr(radar_collector, "_failures", {})


@pytest.fixture
def processed(monkeypatch):
    order = []

    async def process(msg, row, **kwargs):
        order.append((row["chat_ref"], msg.id))
        return False

    monkeypatch.setattr(radar_collector, "process_radar_message", process)
    return order


def _history(monkeypatch, history):
    """history: chat_id -> messages newest first, or an exception to raise."""

    async def get_chat_history(chat_id, limit):
        source = history[chat_id]
        if isinstance(source, Exception):
            raise source
        for m in source:
            yield m

    monkeypatch.setattr(radar_collector.userbot, "get_chat_history", get_chat_history)


def _msg(msg_id, hh, mm, ss):
    return SimpleNamespace(id=msg_id, date=datetime(2026, 9, 14, hh, mm, ss, tzinfo=timezone.utc))


async def _chats(*refs):
    for i, ref in enumerate(refs):
        await add_radar_chat(ref, ref, -1000 - i)
    async with get_db() as conn:
        await conn.execute("UPDATE radar_chats SET last_seen_msg_id = 1")
        await conn.commit()


def test_messages_are_processed_in_posting_order_across_chats(db, monkeypatch, processed):
    """The Golden Code race of 14-09: the repost in the chat polled first claimed
    a code the original chat had carried half a minute earlier."""
    _history(monkeypatch, {
        -1000: [_msg(143530, 7, 16, 6)],
        -1001: [_msg(590850, 7, 15, 43), _msg(590849, 7, 15, 37)],
    })

    async def scenario():
        await _chats("@guthrieechat", "@keydropchat")
        await radar_collector._poll_cycle()
        return {r["chat_ref"]: r["last_seen_msg_id"] for r in await get_radar_chats()}

    last_seen = run(scenario())
    assert processed == [
        ("@keydropchat", 590849), ("@keydropchat", 590850), ("@guthrieechat", 143530)
    ]
    assert last_seen == {"@guthrieechat": 143530, "@keydropchat": 590850}


def test_one_failed_poll_is_not_a_warning_but_two_in_a_row_are(db, monkeypatch, processed, caplog):
    boom = RuntimeError("[500 RPC_CALL_FAIL]")
    _history(monkeypatch, {-1000: boom})

    def warnings():
        return [r for r in caplog.records if r.levelno >= logging.WARNING]

    async def scenario():
        await _chats("@guthrieecs")
        with caplog.at_level(logging.INFO, logger=radar_collector.log.name):
            await radar_collector._poll_cycle()
            assert warnings() == []
            await radar_collector._poll_cycle()
            assert len(warnings()) == 1
            await radar_collector._poll_cycle()
            assert len(warnings()) == 1
            _history(monkeypatch, {-1000: []})
            await radar_collector._poll_cycle()
            await radar_collector._poll_cycle()
            _history(monkeypatch, {-1000: boom})
            await radar_collector._poll_cycle()
            assert len(warnings()) == 1

    run(scenario())
