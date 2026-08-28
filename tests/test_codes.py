"""Code-pattern keywords: shape detection, and the dedup that keeps a reposted
code from ringing twice."""
import asyncio

import pytest

from src.config import settings
from src.db.base import init_db
from src.db.radar import (
    add_radar_keyword,
    count_repeat_codes,
    filter_unseen_codes,
    purge_seen_codes,
    record_seen_codes,
)
from src.db.base import get_db
from src.radar.matcher import find_codes, format_code_spec, parse_code_spec


async def _backdate(code: str, days: int) -> None:
    """Age a sighting, so window behaviour is testable without waiting a week."""
    async with get_db() as conn:
        await conn.execute(
            "UPDATE radar_seen_codes SET last_seen_at = datetime('now', ?) WHERE code = ?",
            (f"-{days} days", code),
        )
        await conn.commit()


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


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("5", [5]),
        ("code:5", [5]),
        ("10,17", [10, 17]),
        ("code: 5 , 17 ", [5, 17]),
        ("16-18", [16, 17, 18]),
        ("17,17", [17]),
        ("golden", []),
        ("", []),
        ("1", []),          # below CODE_MIN_LEN
        ("999", []),        # above CODE_MAX_LEN
        ("5-x", []),
    ],
)
def test_parse_code_spec(spec, expected):
    assert parse_code_spec(spec) == expected


def test_format_round_trips():
    assert format_code_spec(parse_code_spec("17,5")) == "code:5,17"
    assert parse_code_spec(format_code_spec([5, 10])) == [5, 10]


@pytest.mark.parametrize(
    "text,lengths,expected",
    [
        ("R6S9A \nMETA CASE\nEK6WCVEG2GKMFEJSD", [5, 17], ["R6S9A", "EK6WCVEG2GKMFEJSD"]),
        ("GOLDEN CODE - LVR41ED9DUH0UCCPT", [17], ["LVR41ED9DUH0UCCPT"]),
        ("F3QK5 ТГ кейс", [5], ["F3QK5"]),
        ("XAVWETM7CBESYFB8B", [17], ["XAVWETM7CBESYFB8B"]),
        # No digits, but far too consonant-heavy to be a word.
        ("WIBUSZXBHX case zombie attack 0/3", [10], ["WIBUSZXBHX"]),
        # A real all-caps word of the same length is not a code.
        ("TOURNAMENT starts now", [10], []),
        ("CS2 CSGO DOTA VALVE STEAM ADMIN", [5], []),
        # A bare number is a quantity.
        ("платив 10500 грн", [5], []),
        # A code must stand alone, not sit inside a longer token.
        ("1-5LVLFaceIT", [5], []),
        # Links are full of uppercase runs and percent-encoding.
        ("https://faceit.com/x/Keydrop%202V2%20Tournament", [5], []),
        ("t.me/c/1234567890/AB12C", [5, 10], []),
        # Lowercase is never a code.
        ("f3qk5 golden code", [5], []),
    ],
)
def test_find_codes(text, lengths, expected):
    assert find_codes(text, lengths) == expected


def test_find_codes_deduplicates_within_one_message():
    assert find_codes("R6S9A and again R6S9A", [5]) == ["R6S9A"]


def test_find_codes_without_lengths_matches_nothing():
    assert find_codes("R6S9A", []) == []


def test_seen_codes_suppress_a_repeat(db):
    async def scenario():
        first = await filter_unseen_codes(["R6S9A", "F3QK5"], 7)
        assert first == {"R6S9A", "F3QK5"}
        await record_seen_codes(["R6S9A", "F3QK5"], "@chat", "https://t.me/chat/1")

        # The same codes reposted: nothing left to alert on.
        assert await filter_unseen_codes(["R6S9A", "F3QK5"], 7) == set()
        # A new code alongside old ones still gets through.
        assert await filter_unseen_codes(["R6S9A", "XPMR4AZQH5"], 7) == {"XPMR4AZQH5"}

    run(scenario())


def test_repeat_sightings_are_counted(db):
    async def scenario():
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/1")
        assert await count_repeat_codes(7) == 0
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/2")
        await record_seen_codes(["R6S9A"], "@other", "https://t.me/other/9")
        assert await count_repeat_codes(7) == 2

    run(scenario())


def test_dedup_is_global_across_chats(db):
    async def scenario():
        await record_seen_codes(["R6S9A"], "@keydropchat", "https://t.me/keydropchat/1")
        assert await filter_unseen_codes(["R6S9A"], 7) == set()

    run(scenario())


def test_a_code_older_than_the_window_alerts_again(db):
    async def scenario():
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/1")
        assert await filter_unseen_codes(["R6S9A"], 7) == set()
        await _backdate("R6S9A", 8)
        assert await filter_unseen_codes(["R6S9A"], 7) == {"R6S9A"}

    run(scenario())


def test_repeat_count_only_covers_the_window(db):
    async def scenario():
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/1")
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/2")
        assert await count_repeat_codes(7) == 1
        await _backdate("R6S9A", 8)
        assert await count_repeat_codes(7) == 0

    run(scenario())


def test_purge_drops_only_stale_rows(db):
    async def scenario():
        await record_seen_codes(["R6S9A", "F3QK5"], "@chat", "https://t.me/chat/1")
        assert await purge_seen_codes(14) == 0
        await _backdate("R6S9A", 20)
        assert await purge_seen_codes(14) == 1
        # The purged code is free to alert again; the fresh one still guards.
        assert await filter_unseen_codes(["R6S9A", "F3QK5"], 7) == {"R6S9A"}

    run(scenario())


def test_filter_handles_an_empty_batch(db):
    assert run(filter_unseen_codes([], 7)) == set()
    run(record_seen_codes([], "@chat", "url"))


def test_keyword_kind_is_stored(db):
    async def scenario():
        from src.db.radar import get_radar_keywords

        assert await add_radar_keyword("golden")
        assert await add_radar_keyword("code:17", "code")
        by_kw = {r["keyword"]: r["kind"] for r in await get_radar_keywords()}
        assert by_kw == {"golden": "text", "code:17": "code"}

    run(scenario())


@pytest.mark.parametrize(
    "pasted,expected",
    [
        ("F3QK5 R6S9A EK6WCVEG2GKMFEJSD", [5, 17]),
        ("F3QK5, L8J22, XAVWETM7CBESYFB8B", [5, 17]),
        # A whole message copied out of the chat, words and all.
        ("GOLDEN CODE - LVR41ED9DUH0UCCPT", [17]),
        ("R6S9A\nMETA CASE\nEK6WCVEG2GKMFEJSD", [5, 17]),
        # Nothing code-shaped to measure.
        ("hello there friends", []),
        ("f3qk5 r6s9a", []),
        ("", []),
    ],
)
def test_infer_code_lengths(pasted, expected):
    from src.radar.matcher import infer_code_lengths

    assert infer_code_lengths(pasted) == expected


def test_inferred_lengths_then_match_those_codes():
    """What the examples measured must be what the keyword goes on to catch."""
    from src.radar.matcher import infer_code_lengths

    lengths = infer_code_lengths("F3QK5 EK6WCVEG2GKMFEJSD")
    assert find_codes("new drop XPMR4AZQH5 and LVR41ED9DUH0UCCPT", lengths) == [
        "LVR41ED9DUH0UCCPT"
    ]
