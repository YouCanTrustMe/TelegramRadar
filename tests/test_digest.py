"""The weekly digest's presentation: a bare 🔗 is not a tap target, so the
example link has to cover a whole phrase and no keyboard row may be emoji-only."""
import asyncio

import pytest

from src.radar import digest


def run(coro):
    return asyncio.run(coro)


def _row(**over):
    row = {
        "keyword": "кейс", "chat_title": "Гатрай", "chat_ref": "@hatray", "cnt": 14,
        "sample_author": "Shadow", "sample_url": "https://t.me/hatray/1",
        "keyword_id": 1, "chat_db_id": 1,
    }
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def never_reach_telegram(monkeypatch):
    """A test that forgets to stub the sender must fail loudly, not message the admin."""

    async def guard(*args, **kwargs):
        raise AssertionError("test attempted a live Bot API call")

    monkeypatch.setattr("src.dispatcher.sender.send_to", guard)
    monkeypatch.setattr(digest, "send_to", guard)


@pytest.fixture
def sent(monkeypatch):
    box = []

    async def fake_send(chat_id, text, **kwargs):
        box.append({"text": text, **kwargs})

    monkeypatch.setattr(digest, "send_to", fake_send)
    return box


def _stub_rows(monkeypatch, rows):
    async def fake(days):
        return rows

    monkeypatch.setattr(digest, "get_muted_summary_since", fake)


def test_the_example_link_covers_the_whole_phrase(monkeypatch, sent):
    _stub_rows(monkeypatch, [_row()])
    run(digest.send_muted_digest())
    body = sent[0]["text"]
    assert '<a href="https://t.me/hatray/1">14× · latest from Shadow</a>' in body
    assert ">🔗</a>" not in body


def test_every_button_row_carries_words(monkeypatch, sent):
    _stub_rows(monkeypatch, [_row(), _row(keyword_id=None, chat_db_id=None)])
    run(digest.send_muted_digest())
    rows = sent[0]["reply_markup"]["inline_keyboard"]
    for row in rows:
        for button in row:
            assert len(button["text"].split(" ", 1)[-1].strip()) > 1, button


def test_a_group_with_no_editor_falls_back_to_the_message_link(monkeypatch, sent):
    _stub_rows(monkeypatch, [_row(keyword_id=None, chat_db_id=None)])
    run(digest.send_muted_digest())
    first = sent[0]["reply_markup"]["inline_keyboard"][0][0]
    assert first["url"] == "https://t.me/hatray/1"
    assert "кейс" in first["text"]


def test_a_code_keyword_shows_its_shape_not_its_spec(monkeypatch, sent):
    _stub_rows(monkeypatch, [_row(keyword="code:17")])
    run(digest.send_muted_digest())
    body = sent[0]["text"]
    assert "code:17" not in body
    assert "🔑17" in body


def test_nothing_muted_sends_nothing(monkeypatch, sent):
    _stub_rows(monkeypatch, [])
    run(digest.send_muted_digest())
    assert sent == []
