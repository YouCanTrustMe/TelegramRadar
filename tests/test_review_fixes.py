"""Regressions for defects found reviewing the code-keyword change."""
import asyncio
import json

import pytest

from src.config import settings
from src.db.base import init_db
from src.db.radar import (
    add_radar_chat,
    add_radar_keyword,
    add_sender_rule,
    enqueue_pending_alert,
    get_keyword_chat_modes,
    get_muted_summary_since,
    get_radar_chats,
    get_radar_keywords,
    get_recent_senders_for_keyword,
    get_recent_trigger_senders,
    link_keyword_chat,
    log_radar_alert,
    remove_sender_rule,
    remove_sender_rules_for,
    reset_empty_allowlists,
    set_keyword_chat_mode,
)
from src.radar import pending


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "radar.db"))
    run(init_db())


@pytest.fixture(autouse=True)
def never_reach_telegram(monkeypatch):
    async def guard(*args, **kwargs):
        raise AssertionError("test attempted a live Bot API call")

    monkeypatch.setattr("src.dispatcher.sender.send_to", guard)
    monkeypatch.setattr(pending, "send_to", guard)


async def _chat_and_keyword(kw="golden"):
    await add_radar_chat("@keydropchat", "Key-Drop", -100123)
    chat = (await get_radar_chats())[0]
    await add_radar_keyword(kw)
    kw_row = next(k for k in await get_radar_keywords() if k["keyword"] == kw)
    await link_keyword_chat(kw_row["id"], chat["id"])
    return chat, kw_row


def test_a_resent_alert_keeps_its_chat_id(db, monkeypatch):
    """A delivery that went through the resend queue must stay visible in the
    per-chat views, not be orphaned the way a rename used to orphan history."""

    async def scenario():
        chat, kw_row = await _chat_and_keyword()
        entries = [{
            "keyword": "golden", "chat_ref": "@keydropchat", "author_id": 7,
            "message_text": "golden code", "message_url": "https://t.me/keydropchat/5",
            "author_name": "Ann", "chat_db_id": chat["id"],
        }]
        await enqueue_pending_alert(
            "https://t.me/keydropchat/5", "body", None, json.dumps(entries)
        )

        async def ok(*args, **kwargs):
            return None

        monkeypatch.setattr(pending, "send_to", ok)
        assert await pending.flush_pending_alerts() == 1

        senders = await get_recent_trigger_senders("golden", chat["id"])
        assert [s["author_id"] for s in senders] == [7]

    run(scenario())


def test_an_older_queued_alert_without_a_chat_id_still_delivers(db, monkeypatch):
    """Rows queued by the previous build have no chat_db_id key at all."""

    async def scenario():
        chat, _ = await _chat_and_keyword()
        entries = [{
            "keyword": "golden", "chat_ref": "@keydropchat", "author_id": 7,
            "message_text": "golden code", "message_url": "https://t.me/keydropchat/6",
            "author_name": "Ann",
        }]
        await enqueue_pending_alert(
            "https://t.me/keydropchat/6", "body", None, json.dumps(entries)
        )

        async def ok(*args, **kwargs):
            return None

        monkeypatch.setattr(pending, "send_to", ok)
        assert await pending.flush_pending_alerts() == 1

    run(scenario())


def test_the_sender_link_points_at_the_newest_message(db):
    """Message ids are not lexicographically ordered: 9 sorts above 100."""

    async def scenario():
        chat, kw_row = await _chat_and_keyword()
        for msg_id in (9, 100):
            await log_radar_alert(
                "golden", "@keydropchat", 7, "golden code",
                f"https://t.me/keydropchat/{msg_id}", "Ann", "sent", chat["id"],
            )
        picker = await get_recent_trigger_senders("golden", chat["id"])
        assert picker[0]["last_url"] == "https://t.me/keydropchat/100"

        by_keyword = await get_recent_senders_for_keyword(kw_row["id"])
        assert by_keyword[0]["last_url"] == "https://t.me/keydropchat/100"

    run(scenario())


def test_removing_the_last_allowed_sender_reopens_the_keyword(db):
    """An allowlist with nobody on it alerts on nothing — the one filter state
    that looks configured while reporting silence."""

    async def scenario():
        chat, kw_row = await _chat_and_keyword()
        await set_keyword_chat_mode(kw_row["id"], chat["id"], "allowlist")
        await add_sender_rule(kw_row["id"], chat["id"], 7, "allow", "Ann")

        assert await remove_sender_rules_for(7, "allow") == 1
        assert await reset_empty_allowlists() == 1
        modes = await get_keyword_chat_modes()
        assert modes[(kw_row["id"], chat["id"])] == "all"

    run(scenario())


def test_an_allowlist_that_still_has_someone_is_left_alone(db):
    async def scenario():
        chat, kw_row = await _chat_and_keyword()
        await set_keyword_chat_mode(kw_row["id"], chat["id"], "allowlist")
        await add_sender_rule(kw_row["id"], chat["id"], 7, "allow", "Ann")
        await add_sender_rule(kw_row["id"], chat["id"], 8, "allow", "Bob")
        rules_removed = await remove_sender_rules_for(7, "allow")

        assert rules_removed == 1
        assert await reset_empty_allowlists() == 0
        modes = await get_keyword_chat_modes()
        assert modes[(kw_row["id"], chat["id"])] == "allowlist"

    run(scenario())


def test_a_mute_only_sender_never_reopens_an_allowlist(db):
    async def scenario():
        chat, kw_row = await _chat_and_keyword()
        await set_keyword_chat_mode(kw_row["id"], chat["id"], "allowlist")
        await add_sender_rule(kw_row["id"], chat["id"], 7, "allow", "Ann")
        await add_sender_rule(kw_row["id"], chat["id"], 8, "mute", "Bob")
        await remove_sender_rules_for(8, "mute")

        assert await reset_empty_allowlists() == 0

    run(scenario())


def test_orphaned_muted_rows_group_by_their_logged_chat(db):
    """Rows no rename ever re-keyed have a NULL chat id. NULL never equals NULL,
    so they used to collapse into one nameless group with no sample."""

    async def scenario():
        await add_radar_keyword("кейс")
        for ref, url in (
            ("@oldname", "https://t.me/oldname/1"),
            ("@oldname", "https://t.me/oldname/2"),
            ("@othername", "https://t.me/othername/1"),
        ):
            await log_radar_alert("кейс", ref, 7, "кейс", url, "Ann", "muted", None)

        groups = await get_muted_summary_since(7)
        by_ref = {g["chat_ref"]: g for g in groups}
        assert set(by_ref) == {"@oldname", "@othername"}
        assert by_ref["@oldname"]["cnt"] == 2
        # The sample survives the NULL join instead of coming back empty.
        assert by_ref["@oldname"]["sample_url"] == "https://t.me/oldname/2"
        assert by_ref["@oldname"]["sample_author"] == "Ann"

    run(scenario())


def test_a_deleted_rule_reopens_an_emptied_allowlist(db):
    async def scenario():
        chat, kw_row = await _chat_and_keyword()
        await set_keyword_chat_mode(kw_row["id"], chat["id"], "allowlist")
        await add_sender_rule(kw_row["id"], chat["id"], 7, "allow", "Ann")
        from src.db.radar import get_sender_rules_for

        rule = (await get_sender_rules_for(kw_row["id"], chat["id"]))[0]
        await remove_sender_rule(rule["id"])
        assert await reset_empty_allowlists() == 1

    run(scenario())
