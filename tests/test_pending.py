import asyncio
import json

import pytest

from src.config import settings
from src.db.base import get_db, init_db
from src.db.radar import count_pending_alerts, get_recent_radar_alerts
from src.dispatcher.sender import SendFailed
from src.radar import pending

ENTRIES = [
    {
        "keyword": "стрім",
        "chat_ref": "@somechat",
        "author_id": 42,
        "message_text": "буде стрім",
        "message_url": "https://t.me/somechat/1",
        "author_name": "Someone",
    }
]


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

    monkeypatch.setattr(pending, "send_to", guard)


@pytest.fixture
def sent(never_reach_telegram, monkeypatch):
    calls = []

    async def fake_send(chat_id, text, **kwargs):
        calls.append(text)

    monkeypatch.setattr(pending, "send_to", fake_send)
    return calls


@pytest.fixture
def failing(never_reach_telegram, monkeypatch):
    calls = []

    async def fake_send(chat_id, text, **kwargs):
        calls.append(text)
        raise SendFailed("no response from Bot API: TimeoutError()")

    monkeypatch.setattr(pending, "send_to", fake_send)
    return calls


def queue(url="https://t.me/somechat/1", body="alert body", markup=None):
    return run(pending.queue_alert(url, body, markup, ENTRIES))


async def _row():
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM radar_pending_alerts") as cur:
            return await cur.fetchone()


async def _set(**cols):
    assigns = ", ".join(f"{k} = ?" for k in cols)
    async with get_db() as conn:
        await conn.execute(f"UPDATE radar_pending_alerts SET {assigns}", tuple(cols.values()))
        await conn.commit()


def test_alert_is_queued(db):
    assert queue() is True
    assert run(count_pending_alerts()) == 1


def test_same_message_is_never_queued_twice(db):
    assert queue() is True
    assert queue() is False
    assert run(count_pending_alerts()) == 1


def test_resend_delivers_and_clears_the_queue(db, sent):
    queue()
    assert run(pending.flush_pending_alerts()) == 1
    assert run(count_pending_alerts()) == 0
    assert len(sent) == 1


def test_resent_alert_carries_the_marker(db, sent):
    queue(body="alert body")
    run(pending.flush_pending_alerts())
    assert sent[0].startswith("🔁")
    assert sent[0].endswith("alert body")


def test_delivered_resend_reaches_the_alert_log(db, sent):
    queue()
    run(pending.flush_pending_alerts())
    logged = run(get_recent_radar_alerts(5))
    assert [(r["keyword"], r["status"]) for r in logged] == [("стрім", "sent")]


def test_failed_resend_keeps_the_row_and_backs_off(db, failing):
    queue()
    assert run(pending.flush_pending_alerts()) == 0
    row = run(_row())
    assert row["attempts"] == 1
    assert row["last_error"]
    assert run(count_pending_alerts()) == 1


def test_a_deferred_row_is_not_retried_before_it_is_due(db, failing):
    queue()
    run(pending.flush_pending_alerts())
    assert len(failing) == 1
    run(pending.flush_pending_alerts())
    assert len(failing) == 1, "a backed-off row must not be retried in the next cycle"


async def _minutes_until_due():
    async with get_db() as conn:
        async with conn.execute(
            "SELECT CAST(ROUND((julianday(next_attempt_at) - julianday('now')) * 1440) AS INTEGER) "
            "FROM radar_pending_alerts"
        ) as cur:
            return (await cur.fetchone())[0]


def test_the_first_backoff_step_is_used(db, failing):
    queue()
    run(pending.flush_pending_alerts())
    assert run(_minutes_until_due()) == pending._BACKOFF_MINUTES[0]


def test_each_failure_moves_to_the_next_backoff_step(db, failing):
    queue()
    run(pending.flush_pending_alerts())
    run(_set(next_attempt_at="2000-01-01 00:00:00"))
    run(pending.flush_pending_alerts())
    assert run(_row())["attempts"] == 2
    assert run(_minutes_until_due()) == pending._BACKOFF_MINUTES[1]


def test_the_last_backoff_step_is_used_before_giving_up(db, failing):
    queue()
    run(_set(attempts=len(pending._BACKOFF_MINUTES) - 1))
    run(pending.flush_pending_alerts())
    assert run(count_pending_alerts()) == 1, "the final backoff step must still be retried"
    assert run(_minutes_until_due()) == pending._BACKOFF_MINUTES[-1]


def test_resend_gives_up_after_the_last_backoff_step(db, failing):
    queue()
    run(_set(attempts=len(pending._BACKOFF_MINUTES)))
    assert run(pending.flush_pending_alerts()) == 0
    assert run(count_pending_alerts()) == 0
    logged = run(get_recent_radar_alerts(5))
    assert [(r["keyword"], r["status"]) for r in logged] == [("стрім", "failed")]


def test_an_unreadable_row_is_still_delivered(db, sent):
    queue()
    run(_set(log_payload='[{"keyword": "стр'))
    assert run(pending.flush_pending_alerts()) == 1
    assert run(count_pending_alerts()) == 0
    assert len(sent) == 1


def test_an_unreadable_row_never_blocks_the_queue(db, sent):
    queue(url="https://t.me/somechat/1")
    run(_set(log_payload="not json"))
    queue(url="https://t.me/somechat/2")
    assert run(pending.flush_pending_alerts()) == 2


def test_flush_stops_at_the_first_failure(db, failing):
    queue(url="https://t.me/somechat/1")
    queue(url="https://t.me/somechat/2")
    run(pending.flush_pending_alerts())
    assert len(failing) == 1, "an outage must not be hammered once per queued alert"
    assert run(count_pending_alerts()) == 2


def test_reply_markup_survives_the_queue(db, never_reach_telegram, monkeypatch):
    seen = {}

    async def fake_send(chat_id, text, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(pending, "send_to", fake_send)
    markup = {"inline_keyboard": [[{"text": "🔗 Open message", "url": "https://t.me/c/1/2"}]]}
    queue(markup=markup)
    run(pending.flush_pending_alerts())
    assert seen["reply_markup"] == markup


def test_queue_stores_the_payload_verbatim(db):
    queue()
    assert json.loads(run(_row())["log_payload"]) == ENTRIES
