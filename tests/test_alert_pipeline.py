"""End-to-end shape of an alert: which keywords fire, what the body says, and
what the log ends up holding — driven through process_radar_message itself."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.config import settings
from src.db.base import init_db
from src.db.radar import (
    add_radar_chat,
    add_radar_keyword,
    get_radar_chats,
    get_radar_keywords,
    get_recent_radar_alerts,
    link_keyword_chat,
)
from src.radar import handlers


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "radar.db"))
    run(init_db())


@pytest.fixture(autouse=True)
def never_reach_telegram(monkeypatch):
    """A test that forgets to stub the sender must fail loudly, not message the admin."""

    async def guard(*args, **kwargs):
        raise AssertionError("test attempted a live Bot API call")

    monkeypatch.setattr("src.dispatcher.sender.send_to", guard)
    monkeypatch.setattr(handlers, "send_to", guard)


@pytest.fixture
def sent(monkeypatch, never_reach_telegram):
    """Capture what would have gone to the admin instead of sending it."""
    calls = []

    async def fake_send_to(chat_id, text, **kwargs):
        calls.append({"chat_id": chat_id, "text": text, **kwargs})

    monkeypatch.setattr(handlers, "send_to", fake_send_to)
    return calls


def _message(text, msg_id=100):
    return SimpleNamespace(
        id=msg_id,
        text=text,
        caption=None,
        date=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
        chat=SimpleNamespace(id=-1001234567890, username="keydropchat", title="Key-Drop"),
        from_user=SimpleNamespace(id=555, first_name="Ann", last_name=None, username="ann"),
        sender_chat=None,
        is_automatic_forward=False,
        forward_from_chat=None,
    )


async def _setup(keywords):
    """keywords: list of (text, kind). Returns (chat_row, keyword_rows, linked_ids)."""
    await add_radar_chat("@keydropchat", "Key-Drop", -1001234567890)
    chat_row = (await get_radar_chats())[0]
    for kw, kind in keywords:
        await add_radar_keyword(kw, kind)
    kw_rows = await get_radar_keywords()
    for row in kw_rows:
        await link_keyword_chat(row["id"], chat_row["id"])
    return chat_row, kw_rows, {r["id"] for r in kw_rows}


async def _process(text, keywords, msg_id=100):
    chat_row, kw_rows, linked = await _setup(keywords)
    return await handlers.process_radar_message(
        _message(text, msg_id), chat_row, keywords=kw_rows, linked_kw_ids=linked
    )


def test_a_code_alerts_and_is_shown_tap_to_copy(db, sent):
    assert run(_process("GOLDEN CODE - LVR41ED9DUH0UCCPT", [("code:17", "code")]))
    body = sent[0]["text"]
    assert "<code>LVR41ED9DUH0UCCPT</code>" in body
    assert "🔑 Code:" in body
    # The spec itself is bookkeeping, not something to read in an alert.
    assert "code:17" not in body


def test_a_repeated_code_stays_quiet(db, sent):
    async def scenario():
        chat_row, kw_rows, linked = await _setup([("code:5", "code")])

        async def process(msg_id):
            return await handlers.process_radar_message(
                _message("F3QK5 ТГ кейс", msg_id), chat_row,
                keywords=kw_rows, linked_kw_ids=linked,
            )

        assert await process(1) is True
        assert await process(2) is False
        assert await process(3) is False

    run(scenario())
    assert len(sent) == 1


def test_a_new_code_still_alerts_after_a_repeat(db, sent):
    async def scenario():
        chat_row, kw_rows, linked = await _setup([("code:5", "code")])

        async def process(text, msg_id):
            return await handlers.process_radar_message(
                _message(text, msg_id), chat_row, keywords=kw_rows, linked_kw_ids=linked
            )

        assert await process("F3QK5", 1) is True
        assert await process("F3QK5 and R6S9A", 2) is True
        # Only the unseen code is worth showing.
        assert "R6S9A" in sent[1]["text"]

    run(scenario())


def test_word_and_code_keywords_appear_in_separate_sections(db, sent):
    assert run(_process(
        "golden code EK6WCVEG2GKMFEJSD", [("golden", "text"), ("code:17", "code")]
    ))
    body = sent[0]["text"]
    assert "🔍 Keyword:\n<blockquote>golden</blockquote>" in body
    assert "🔑 Code:\n<blockquote><code>EK6WCVEG2GKMFEJSD</code></blockquote>" in body


def test_a_word_only_match_has_no_code_section(db, sent):
    assert run(_process("golden code?", [("golden", "text"), ("code:17", "code")]))
    assert "🔑" not in sent[0]["text"]


def test_a_message_with_no_match_sends_nothing(db, sent):
    assert run(_process("just chatting", [("golden", "text"), ("code:5", "code")])) is False
    assert sent == []


def test_the_log_records_the_stable_chat_id(db, sent):
    async def scenario():
        chat_row, kw_rows, linked = await _setup([("golden", "text")])
        await handlers.process_radar_message(
            _message("golden code"), chat_row, keywords=kw_rows, linked_kw_ids=linked
        )
        row = (await get_recent_radar_alerts(1))[0]
        # Keyed by id, not by the @username a rename can change underneath it.
        assert row["chat_db_id"] == chat_row["id"]
        assert row["chat_ref"] == "@keydropchat"

    run(scenario())


def test_a_muted_sender_is_logged_quietly_with_the_chat_id(db, sent):
    async def scenario():
        chat_row, kw_rows, linked = await _setup([("golden", "text")])
        kw_id = kw_rows[0]["id"]
        await handlers.process_radar_message(
            _message("golden code"), chat_row,
            keywords=kw_rows, linked_kw_ids=linked,
            rules={(kw_id, chat_row["id"], 555): "mute"},
        )
        row = (await get_recent_radar_alerts(1))[0]
        assert row["status"] == "muted"
        assert row["chat_db_id"] == chat_row["id"]

    run(scenario())
    assert sent == []


def test_a_muted_sender_cannot_burn_a_code_for_everyone(db, sent):
    """A code is spent only once it has actually reached the admin. Recording it
    while the sender filter was swallowing it let a muted spammer silence the
    code for the next, legitimate sender too."""
    async def scenario():
        chat_row, kw_rows, linked = await _setup([("code:5", "code")])
        kw_id = kw_rows[0]["id"]
        spammer_muted = {(kw_id, chat_row["id"], 555): "mute"}

        assert await handlers.process_radar_message(
            _message("F3QK5", 1), chat_row,
            keywords=kw_rows, linked_kw_ids=linked, rules=spammer_muted,
        ) is False
        assert sent == []

        # Same code, a sender who is not muted: the admin has still never seen it.
        assert await handlers.process_radar_message(
            _message("F3QK5", 2), chat_row, keywords=kw_rows, linked_kw_ids=linked
        ) is True

    run(scenario())
    assert len(sent) == 1
    assert "F3QK5" in sent[0]["text"]


def test_a_delivered_code_is_still_spent(db, sent):
    """The dedup must keep working for codes that did reach the admin."""
    async def scenario():
        chat_row, kw_rows, linked = await _setup([("code:5", "code")])

        async def process(msg_id):
            return await handlers.process_radar_message(
                _message("F3QK5", msg_id), chat_row,
                keywords=kw_rows, linked_kw_ids=linked,
            )

        assert await process(1) is True
        assert await process(2) is False

    run(scenario())
    assert len(sent) == 1


def test_the_alert_carries_a_filter_button_per_matched_keyword(db, sent):
    assert run(_process(
        "golden code EK6WCVEG2GKMFEJSD", [("golden", "text"), ("code:17", "code")]
    ))
    rows = sent[0]["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["url"].endswith("/keydropchat/100")
    mutes = [b["callback_data"] for r in rows[1:] for b in r if b["callback_data"].startswith("rmute:")]
    assert len(mutes) == 2
