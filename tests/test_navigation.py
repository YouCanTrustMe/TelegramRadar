"""Navigation wiring: every callback a screen offers must have a handler, and
every command in the "/" menu must be registered."""
import re
from pathlib import Path

import pytest

# Imported at collection time on purpose: pyrogram grabs the event loop when the
# bot client module loads, which raises once a test has already run asyncio.run.
from src.bot.commands import _MENU

SRC = Path(__file__).resolve().parent.parent / "src"

# Buttons that carry a URL or are deliberately inert have no handler.
_INERT = {"noop"}

# `_radar_list_kb` is generic: its prefixes arrive as arguments, so the literal
# callback cannot be reconstructed from the source. Its callers pass concrete
# strings ("radar_kw_del:", "radar_chats", ...) which this test does check where
# they appear at the call site.
_BUILDER_PARAMS = {"del_prefix", "list_cb_base", "add_cb", "view_prefix", "label_cb"}


def _sources() -> str:
    return "\n".join(p.read_text() for p in SRC.rglob("*.py"))


def _handler_patterns() -> list[re.Pattern]:
    """The regexes each on_callback_query is registered with."""
    return [
        re.compile(p)
        for p in re.findall(r'pf\.regex\(r"(\^[^"]+\$)"\)', _sources())
    ]


def _emitted_callbacks() -> set[str]:
    """Literal callback_data values the UI hands to Telegram, with f-string
    placeholders filled in by a plausible value so they can be matched."""
    out = set()
    for raw in re.findall(r'callback_data=f?"([^"]+)"', _sources()):
        out.add(raw)
    for raw in re.findall(r'"callback_data":\s*f?"([^"]+)"', _sources()):
        out.add(raw)
    return out


# Matched against the whole placeholder expression, most specific first.
_SUBSTITUTIONS = [
    ("target", "1:1:1"),   # f"{chat_db_id}:{kw_id}:{author_id}"
    ("code", "ABC12"),
    ("action", "mute"),
    ("other", "allow"),
    ("mode", "all"),
]


def _concretise(cb: str) -> str | None:
    """Replace {placeholders} with something a handler regex would accept.

    Returns None when the callback is assembled from arguments and so cannot be
    reconstructed by reading the source."""
    if "{" not in cb:
        return cb
    if any(param in cb for param in _BUILDER_PARAMS):
        return None

    def repl(m: re.Match) -> str:
        expr = m.group(1)
        for key, value in _SUBSTITUTIONS:
            if key in expr:
                return value
        return "1"

    return re.sub(r"\{([^}]*)\}", repl, cb)


@pytest.mark.parametrize("callback", sorted(_emitted_callbacks()))
def test_every_button_has_a_handler(callback):
    concrete = _concretise(callback)
    if concrete is None or concrete in _INERT:
        return
    assert any(p.match(concrete) for p in _handler_patterns()), (
        f"button emits callback_data {callback!r} (as {concrete!r}) "
        f"but no on_callback_query regex matches it"
    )


def test_every_menu_command_is_registered():
    registered = set(re.findall(r'pf\.command\("(\w+)"\)', _sources()))
    missing = {name for name, _ in _MENU} - registered
    assert not missing, f"menu advertises commands with no handler: {sorted(missing)}"


def test_every_registered_command_is_in_the_menu():
    registered = set(re.findall(r'pf\.command\("(\w+)"\)', _sources()))
    # /start is an alias for /radar and is deliberately not advertised.
    undocumented = registered - {name for name, _ in _MENU} - {"start"}
    assert not undocumented, f"commands missing from the menu: {sorted(undocumented)}"


def test_menu_descriptions_fit_telegram_limits():
    for name, description in _MENU:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name), name
        assert 1 <= len(description) <= 256, name
