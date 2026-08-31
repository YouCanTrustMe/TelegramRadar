"""/logs must never build a message Telegram will refuse: one verbose DEBUG line
can be several hundred characters, and escaping only makes it longer."""
from html import escape

from src.bot.handlers.misc import _TELEGRAM_TEXT_LIMIT, _fit_message


def _rendered(lines: list[str]) -> str:
    return f"📄 <b>Log</b> · last {len(lines)} lines\n<pre>" + escape("\n".join(lines)) + "</pre>"


def test_a_long_tail_is_trimmed_to_fit():
    lines = [f"{i:02d} " + "x" * 400 for i in range(20)]
    fitted = _fit_message(lines)
    assert 0 < len(fitted) < len(lines)
    assert len(_rendered(fitted)) <= _TELEGRAM_TEXT_LIMIT


def test_the_newest_lines_are_the_ones_kept():
    lines = [f"{i:02d} " + "x" * 400 for i in range(20)]
    assert _fit_message(lines)[-1] == lines[-1]


def test_escaping_counts_toward_the_budget():
    # 20 lines of 200 raw chars fit; as &lt;/&amp; they are 5-6x longer and do not.
    lines = ["<" * 200 for _ in range(20)]
    fitted = _fit_message(lines)
    assert len(_rendered(fitted)) <= _TELEGRAM_TEXT_LIMIT


def test_a_short_tail_is_untouched():
    lines = [f"line {i}" for i in range(20)]
    assert _fit_message(lines) == lines
