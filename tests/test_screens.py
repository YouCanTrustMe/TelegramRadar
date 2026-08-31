"""Every screen renders, and none of them renders badly: no empty body, no
button that is only an emoji, and no label long enough for Telegram to clip it."""
import asyncio

import pytest

# Imported at collection time on purpose: pyrogram grabs the event loop when the
# bot client module loads, which raises once a test has already run asyncio.run.
from src.bot.handlers import radar as r
from src.bot.handlers import radar_chats as rch
from src.bot.handlers import radar_common as rc
from src.bot.handlers import radar_filters as rf
from src.config import settings
from src.db.base import init_db
from src.db.radar import (
    add_radar_chat,
    add_radar_keyword,
    add_sender_rule,
    block_code,
    get_radar_chats,
    get_radar_keywords,
    link_keyword_chat,
    log_radar_alert,
)

# Telegram clips an inline button somewhere past this; a label that long is a
# design bug either way.
_MAX_BUTTON_LABEL = 40

# Rows that are a bare glyph on purpose: they sit beside a labelled button in the
# same row, where the label says what the row is about.
_ALLOWED_BARE = {"❌", "✅", "🔇", "‹", "›"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "radar.db"))
    run(init_db())


async def _seed():
    await add_radar_chat("@kd", "Key-Drop", -100123)
    chat = (await get_radar_chats())[0]
    await add_radar_keyword("кейс", "text")
    await add_radar_keyword("code:17", "code")
    for k in await get_radar_keywords():
        await link_keyword_chat(k["id"], chat["id"])
    kw = next(k for k in await get_radar_keywords() if k["keyword"] == "кейс")
    await add_sender_rule(kw["id"], chat["id"], 555, "mute", "Shadow")
    await log_radar_alert(
        "кейс", "@kd", 555, "де кейс", "https://t.me/kd/1", "Shadow", "muted", chat["id"]
    )
    await block_code("10LVL")
    return chat, kw


async def _all_screens():
    chat, kw = await _seed()
    return {
        "main": (None, await rc._radar_main_kb()),
        "keywords": await rc._render_keywords(0),
        "chats": await rc._render_chats(0),
        "quiet": await rf.render_quiet_hub(),
        "suppressed": (await rf._render_muted(), None),
        "blocked codes": await rf._render_blocked_codes(),
        "filter keywords": await rf._render_filter_keywords(chat["id"]),
        "filter editor": await rf._render_filter_editor(chat["id"], kw["id"]),
        "last 10": await rf._render_last10(chat["id"], kw["id"]),
        "keyword senders": await rf._render_keyword_senders(kw["id"]),
        "sender list": await rf._render_sender_list("mute", 0),
        "sender detail": await rf._render_sender_detail(555),
        "chat editor": await rch._render_chat_edit(chat["id"], 0),
        "status": (await r._render_status(), None),
    }


@pytest.fixture
def screens(db):
    return run(_all_screens())


def test_every_screen_has_a_bold_title(screens):
    for name, (text, _) in screens.items():
        if text is None:
            continue
        assert text.strip(), name
        assert text.startswith(("📋", "💬", "🔇", "🚫", "⚙️", "👥", "👤", "📊")), (name, text[:20])
        assert "<b>" in text.split("\n", 1)[0], (name, text[:60])


def test_no_button_is_a_bare_emoji_on_its_own_row(screens):
    for name, (_, kb) in screens.items():
        if kb is None:
            continue
        for row in kb.inline_keyboard:
            if len(row) > 1:
                continue
            label = row[0].text.strip()
            assert label not in _ALLOWED_BARE, (name, label)
            assert len(label.split(" ", 1)[-1]) > 1, (name, label)


def test_no_button_label_is_long_enough_to_clip(screens):
    for name, (_, kb) in screens.items():
        if kb is None:
            continue
        for row in kb.inline_keyboard:
            for button in row:
                assert len(button.text) <= _MAX_BUTTON_LABEL, (name, button.text)


def test_a_code_keyword_never_shows_its_raw_spec(screens):
    for name, (text, kb) in screens.items():
        assert "code:17" not in (text or ""), name
        if kb is None:
            continue
        for row in kb.inline_keyboard:
            for button in row:
                assert "code:17" not in button.text, (name, button.text)


def test_a_clipped_label_breaks_on_a_word_and_says_so():
    long_name = "Дуже довга назва каналу про щось важливе · 3×"
    clipped = rc._clip(long_name)
    assert len(clipped) <= _MAX_BUTTON_LABEL
    assert clipped.endswith("…")
    assert not clipped.rstrip("…").endswith(" ")
    # A word is never cut in half.
    assert long_name.startswith(clipped.rstrip("…"))
    assert clipped.rstrip("…").split()[-1] in long_name.split()


def test_a_short_label_is_left_alone():
    assert rc._clip("кейс · Гатрай — 14×") == "кейс · Гатрай — 14×"


def test_recent_senders_link_to_the_message(db):
    async def go():
        chat, kw = await _seed()
        await log_radar_alert(
            "кейс", "@kd", 777, "де кейс", "https://t.me/kd/7", "Ann", "sent", chat["id"]
        )
        _, kb = await rf._render_last10(chat["id"], kw["id"])
        return kb

    kb = run(go())
    named = [row[0] for row in kb.inline_keyboard if "Ann" in row[0].text]
    assert named, "the sender is not listed"
    assert named[0].url == "https://t.me/kd/7"
