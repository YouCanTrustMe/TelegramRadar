import logging
import re
import unicodedata
from functools import lru_cache
from typing import NamedTuple

log = logging.getLogger(__name__)

_CONFUSABLES = {
    "a": "а", "b": "б", "c": "с", "e": "е", "h": "н", "i": "і",
    "k": "к", "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    "α": "а", "ε": "е", "ι": "і", "κ": "к", "μ": "м", "ν": "н",
    "ο": "о", "π": "п", "ρ": "р", "τ": "т", "χ": "х",
}

_LEET = {"0": "о", "1": "і", "3": "е", "4": "а", "6": "б", "8": "в"}

# Tuned for the place names this radar sees (Zagreb, Rijeka, Osijek, Lviv), not
# for official Ukrainian romanisation. No `yi` digraph: it would swallow the `y`
# in "Kyiv".
_TRANSLIT = [
    ("shch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"),
    ("sh", "ш"), ("yu", "ю"), ("ya", "я"), ("ye", "є"), ("je", "є"),
    ("ju", "ю"), ("ja", "я"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("h", "г"), ("g", "г"), ("d", "д"),
    ("e", "е"), ("z", "з"), ("y", "и"), ("i", "і"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
    ("t", "т"), ("u", "у"), ("f", "ф"), ("c", "ц"), ("j", "й"), ("q", "к"),
    ("w", "в"), ("x", "кс"),
]

# A keyword at least this long may match inside a word, catching evasion by word
# merging; a shorter one must start at a word boundary or it fires on any word
# that happens to contain it.
MERGE_MIN_LEN = 5

# Below this length an edit-distance match confuses a typo with a different word,
# so short keywords are compared literally.
FUZZY_MIN_LEN = 5

_LATIN_RUN = re.compile(r"[a-z]{3,}")


def _fuzzy_budget(kw_len: int) -> int:
    if kw_len < FUZZY_MIN_LEN:
        return 0
    return 1 if kw_len < 9 else 2


class Norm(NamedTuple):
    glued: str
    starts: set[int]
    tokens: list[str]


def _normalize(s: str, *, leet: bool = True, keep_digits: bool = False) -> Norm:
    """Fold a string to bare letters.

    `glued` drops separators and collapses repeated letters, so a keyword spelled
    out with spaces or stretched vowels still reads as itself; `starts` marks
    where each word begins in it. `tokens` are the words kept whole and separate:
    when a word ends on the letter the next one opens with, the two share a
    character in `glued` but must not merge into one token.

    With keep_digits, digits survive as themselves rather than folding into
    letters, so a count in a keyword is compared literally."""
    s = unicodedata.normalize("NFKC", s).lower()
    out: list[str] = []
    starts: set[int] = set()
    tokens: list[str] = []
    word: list[str] = []
    at_boundary = True

    def flush() -> None:
        if word:
            tokens.append("".join(word))
            word.clear()

    for ch in s:
        if keep_digits and ch.isdigit():
            mapped = ch
        else:
            mapped = _CONFUSABLES.get(ch) or (_LEET.get(ch) if leet else None) or ch
        if not (mapped.isalpha() or (keep_digits and mapped.isdigit())):
            flush()
            at_boundary = True
            continue
        # Only letters get stretched for emphasis; "22" must stay "22".
        stretchable = mapped.isalpha()
        dup_in_glued = stretchable and bool(out) and out[-1] == mapped
        dup_in_word = stretchable and bool(word) and word[-1] == mapped
        if at_boundary:
            # A word opening with the letter the previous one closed on shares
            # that character in `glued`, yet still starts a word there.
            starts.add(len(out) - 1 if dup_in_glued else len(out))
            at_boundary = False
        if not dup_in_glued:
            out.append(mapped)
        if not dup_in_word:
            word.append(mapped)
    flush()
    return Norm("".join(out), starts, tokens)


def _transliterate(s: str) -> str:
    """Rewrite runs of 3+ latin letters as Cyrillic, so a place name typed in
    latin reaches a Cyrillic keyword. Scattered latin homoglyphs inside a mostly
    Cyrillic word are left to _CONFUSABLES."""

    def repl(m: re.Match) -> str:
        word = m.group(0)
        for lat, cyr in _TRANSLIT:
            word = word.replace(lat, cyr)
        return word

    return _LATIN_RUN.sub(repl, s.lower())


def _osa(a: str, b: str, max_d: int) -> int:
    """Optimal string alignment distance, or exactly max_d + 1 once it exceeds
    max_d — callers rely on that sentinel to tell "one edit too far" from "not
    remotely close". Unlike plain Levenshtein a transposition counts as one edit."""
    la, lb = len(a), len(b)
    if abs(la - lb) > max_d:
        return max_d + 1
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
        if min(cur) > max_d:
            return max_d + 1
        prev2, prev = prev, cur
    # The row-min cut-off is only a lower bound, so the final cell can still
    # overshoot; clamp it or the sentinel stops meaning one thing.
    return prev[lb] if prev[lb] <= max_d else max_d + 1


def _substring_hit(kw: str, norm: Norm, *, numeric: bool = False, merge_min_len: int = MERGE_MIN_LEN) -> bool:
    at = norm.glued.find(kw)
    while at != -1:
        starts_word = at in norm.starts
        if numeric:
            # Anchor both ends of a count, otherwise "20" is found at the head
            # of "2020" and "5" at the head of "50".
            end = at + len(kw)
            runs_on = kw[-1].isdigit() and end < len(norm.glued) and norm.glued[end].isdigit()
            if starts_word and not runs_on:
                return True
        elif starts_word or len(kw) >= merge_min_len:
            return True
        at = norm.glued.find(kw, at + 1)
    return False


def _fuzzy_hit(kw: str, budget: int, tokens: list[str]) -> bool:
    for tok in tokens:
        # An unequal first letter is nearly always a different word rather than
        # an evasion: evaders keep the word recognisable.
        if not tok or tok[0] != kw[0]:
            continue
        # Search one edit past the budget so a true near miss is distinguishable
        # from a token that merely shares a first letter.
        d = _osa(tok, kw, budget + 1)
        if d <= budget:
            return True
        if d == budget + 1:
            log.debug("Radar: near miss token=%r keyword=%r distance=%d budget=%d", tok, kw, d, budget)
    return False


class _Keyword(NamedTuple):
    raw: str
    normalized: str
    numeric: bool
    budget: int


@lru_cache(maxsize=512)
def _compile(raw: str, leet: bool) -> _Keyword:
    """Keywords change once per poll cycle, messages arrive constantly — fold each
    keyword once and let the per-message loop read fields instead of recomputing."""
    if any(ch.isdigit() for ch in raw):
        # A count is the payload, not decoration: it is compared literally, which
        # rules out leetspeak folding, transliteration and edit distance.
        return _Keyword(raw, _normalize(raw, keep_digits=True).glued, True, 0)
    normalized = _normalize(raw, leet=leet).glued
    return _Keyword(raw, normalized, False, _fuzzy_budget(len(normalized)))


def match_keywords(
    text: str,
    keywords: list[str],
    *,
    leet: bool = True,
    fuzzy: bool = True,
    translit: bool = True,
    merge_min_len: int = MERGE_MIN_LEN,
) -> list[str]:
    variants = [_normalize(text, leet=leet)]
    if translit:
        folded = _transliterate(text)
        if folded != text.lower():
            variants.append(_normalize(folded, leet=leet))

    numeric_variant = None

    matched = []
    for kw in keywords:
        compiled = _compile(kw, leet)
        if not compiled.normalized:
            continue
        if compiled.numeric:
            if numeric_variant is None:
                numeric_variant = _normalize(text, keep_digits=True)
            if _substring_hit(compiled.normalized, numeric_variant, numeric=True):
                matched.append(kw)
            continue

        hit = any(
            _substring_hit(compiled.normalized, v, merge_min_len=merge_min_len) for v in variants
        )
        if not hit and fuzzy and compiled.budget:
            hit = any(_fuzzy_hit(compiled.normalized, compiled.budget, v.tokens) for v in variants)
        if hit:
            matched.append(kw)
    return matched
