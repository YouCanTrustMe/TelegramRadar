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
    find_code_variants,
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
        # A short lowercase run is chat, not a code.
        ("f3qk5 golden code", [5], []),
        # A long code retyped in lowercase, or phone-capitalised, is still one.
        ("c272pajwfwdvqxwlt", [17], ["C272PAJWFWDVQXWLT"]),
        ("Qsevmfkn2duy4gvpf", [17], ["QSEVMFKN2DUY4GVPF"]),
        # Case-blind reading needs a digit, and no camelCase.
        ("hmmmmmmmmmmmmmmmm", [17], []),
        ("1-5LVLFaceIT2", [11], []),
        # A username is never a code, whatever it looks like.
        ("▪️ @Br1ghtdown\n▪️ @wishscarlet390", [10, 14], []),
        ("1. @name_surname404 2. @ABC12", [5, 10], []),
        ("e0ve6arhretwu0wjy \nE95VRLQMZG4BSJKY6\nGolden Code Keydrop", [5, 17],
         ["E0VE6ARHRETWU0WJY", "E95VRLQMZG4BSJKY6"]),
    ],
)
def test_find_codes(text, lengths, expected):
    assert find_codes(text, lengths) == expected


def test_find_codes_deduplicates_within_one_message():
    assert find_codes("R6S9A and again R6S9A", [5]) == ["R6S9A"]


def test_find_codes_deduplicates_across_case():
    assert find_codes("C272PAJWFWDVQXWLT c272pajwfwdvqxwlt", [17]) == ["C272PAJWFWDVQXWLT"]


def test_an_o_zero_variant_is_linked_to_the_first_guess(db):
    async def scenario():
        await record_seen_codes(["F5XPXGQSAH7030GXG"], "@keydropchat", "u1")
        variants = await find_code_variants(
            ["F5XPXGQSAH7O3OGXG", "EK6WCVEG2GKMFEJSD"], 7
        )
        assert variants == {"F5XPXGQSAH7O3OGXG": "F5XPXGQSAH7030GXG"}

    run(scenario())


def test_a_variant_outside_the_window_is_a_new_code(db):
    async def scenario():
        await record_seen_codes(["F5XPXGQSAH7030GXG"], "@keydropchat", "u1")
        await _backdate("F5XPXGQSAH7030GXG", 8)
        assert await find_code_variants(["F5XPXGQSAH7O3OGXG"], 7) == {}

    run(scenario())


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


def test_repeated_codes_are_counted(db):
    """One code that keeps coming back counts once, not once per sighting."""
    async def scenario():
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/1")
        assert await count_repeat_codes() == 0
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/2")
        await record_seen_codes(["R6S9A"], "@other", "https://t.me/other/9")
        assert await count_repeat_codes() == 1
        await record_seen_codes(["F3QK5"], "@chat", "https://t.me/chat/3")
        await record_seen_codes(["F3QK5"], "@chat", "https://t.me/chat/4")
        assert await count_repeat_codes() == 2

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


def test_the_purge_is_what_bounds_the_repeat_count(db):
    """`hits` counts a code's whole life, so the count cannot be sliced by a
    window — dropping the row on retention is what retires it."""
    async def scenario():
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/1")
        await record_seen_codes(["R6S9A"], "@chat", "https://t.me/chat/2")
        assert await count_repeat_codes() == 1
        await _backdate("R6S9A", 20)
        assert await count_repeat_codes() == 1
        await purge_seen_codes(14)
        assert await count_repeat_codes() == 0

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
        ("c272pajwfwdvqxwlt", [17]),
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


def test_a_huge_range_is_clamped_before_it_is_built():
    """The bounds are user input on a single-threaded bot: "1-50000000" must not
    materialise fifty million integers before the 3..40 filter sees them."""
    import time

    from src.radar.matcher import parse_code_spec

    start = time.perf_counter()
    assert parse_code_spec("1-9999999999") == [3, 4, 5, 6, 7, 8, 9, 10]
    assert time.perf_counter() - start < 0.5


def test_a_blocked_code_never_alerts_again(db):
    """A token that only looks like a code is blocked by value: muting its sender
    is not an option when that sender also posts the genuine ones."""

    async def scenario():
        from src.db.radar import block_code

        await block_code("10LVL")
        assert await filter_unseen_codes(["10LVL"], 7) == set()
        # Still blocked long after the dedup window would have expired.
        await _backdate("10LVL", 400)
        assert await filter_unseen_codes(["10LVL"], 7) == set()
        # Real codes alongside it are unaffected.
        assert await filter_unseen_codes(["10LVL", "F3QK5"], 7) == {"F3QK5"}

    run(scenario())


def test_blocking_survives_the_purge(db):
    async def scenario():
        from src.db.radar import block_code, get_blocked_codes

        await record_seen_codes(["10LVL", "F3QK5"], "@c", "https://t.me/c/1")
        await block_code("10LVL")
        await _backdate("10LVL", 400)
        await _backdate("F3QK5", 400)

        assert await purge_seen_codes(14) == 1
        assert [r["code"] for r in await get_blocked_codes()] == ["10LVL"]
        assert await filter_unseen_codes(["10LVL"], 7) == set()

    run(scenario())


def test_a_code_can_be_blocked_before_it_is_ever_seen(db):
    async def scenario():
        from src.db.radar import block_code, get_blocked_codes

        await block_code("NEVER1")
        assert [r["code"] for r in await get_blocked_codes()] == ["NEVER1"]
        assert await filter_unseen_codes(["NEVER1"], 7) == set()

    run(scenario())


def test_unblocking_lets_a_code_alert_again(db):
    async def scenario():
        from src.db.radar import block_code, get_blocked_codes, unblock_code

        await block_code("10LVL")
        assert await unblock_code("10LVL") is True
        assert await get_blocked_codes() == []
        assert await filter_unseen_codes(["10LVL"], 7) == {"10LVL"}
        # Unblocking something that was not blocked is a no-op, not an error.
        assert await unblock_code("10LVL") is False

    run(scenario())


def test_blocking_does_not_disturb_the_repeat_counter(db):
    async def scenario():
        from src.db.radar import block_code

        await block_code("10LVL")
        assert await count_repeat_codes() == 0

    run(scenario())
