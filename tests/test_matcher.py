import pytest

from src.radar.matcher import _normalize, _osa, _transliterate, match_keywords

KW = ["стрім"]


def matched(text, keywords=None, **kw):
    return match_keywords(text, keywords or KW, **kw)


@pytest.mark.parametrize(
    "text",
    [
        "стрім",
        "буде стрім сьогодні",
        "СТРІМ",
        "стріми о шостій",
        "стрііііім",
        "с.т.р.і.м",
        "с т р і м",
        "с-т-р-і-м",
        "с​т​р​і​м",
        "cтpiм",
        "сτρім",
        "купистрім",
    ],
)
def test_plain_and_obfuscated(text):
    assert matched(text) == ["стрім"]


@pytest.mark.parametrize("text", ["стр1м", "cтp1м", "0дин стрім"])
def test_leet_digits(text):
    assert matched(text) == ["стрім"]


def test_leet_can_be_disabled():
    assert matched("стр1м", leet=True) == ["стрім"]
    assert matched("стр1м", leet=False) == []


@pytest.mark.parametrize("text", ["strim", "буде strim сьогодні", "STRIM"])
def test_transliteration(text):
    assert matched(text) == ["стрім"]


def test_translit_can_be_disabled():
    assert matched("strim", translit=False) == []


@pytest.mark.parametrize("text", ["стірм", "стрмі", "стqрім", "стім", "стріім"])
def test_fuzzy_typos(text):
    assert matched(text) == ["стрім"]


def test_fuzzy_can_be_disabled():
    assert matched("стірм", fuzzy=True) == ["стрім"]
    assert matched("стірм", fuzzy=False) == []


@pytest.mark.parametrize(
    "text,keyword",
    [
        ("контакт", "акт"),
        ("зарік дав", "рік"),
        ("підстава", "став"),
    ],
)
def test_short_keyword_must_start_a_word(text, keyword):
    assert match_keywords(text, [keyword]) == []


@pytest.mark.parametrize("text,keyword", [("рікша проїхав", "рік"), ("стріми", "стрім")])
def test_prefix_still_matches_so_inflections_are_caught(text, keyword):
    assert match_keywords(text, [keyword]) == [keyword]


@pytest.mark.parametrize(
    "text,keyword",
    [
        ("акт приймання", "акт"),
        ("цей рік", "рік"),
        ("став ясно", "став"),
    ],
)
def test_short_keyword_matches_at_word_start(text, keyword):
    assert match_keywords(text, [keyword]) == [keyword]


def test_neighbouring_words_do_not_fire():
    # Two edits away, so distance alone rejects it.
    assert matched("почувся грім") == []
    assert matched("грім і стрім") == ["стрім"]


def test_first_letter_guard():
    """One edit away but a different opening letter is refused. The cost is that
    an evasion which mangles the first letter slips through too."""
    assert matched("буде трім") == []


def test_fuzzy_false_positive_is_the_known_trade_off():
    """Fuzzy cannot tell a typo from a real neighbouring word. Documented, not fixed:
    the escape hatch is radar_match_fuzzy=False."""
    assert matched("влучив стріл") == ["стрім"]
    assert matched("влучив стріл", fuzzy=False) == []


def test_multiple_keywords_report_original_spelling():
    assert match_keywords("СТР1М та Дрон", ["стрім", "дрон", "танк"]) == ["стрім", "дрон"]


def test_empty_and_noise():
    assert match_keywords("", KW) == []
    assert match_keywords("...", KW) == []
    assert match_keywords("текст", [""]) == []
    assert match_keywords("зовсім інше речення", KW) == []


def test_word_boundary_survives_collapsed_duplicate():
    norm = _normalize("мама аня")
    assert norm.glued == "маманя"
    assert 3 in norm.starts
    assert norm.tokens == ["мама", "аня"]
    assert match_keywords("мама аня", ["аня"]) == ["аня"]


def test_shared_letter_across_a_boundary_does_not_merge_tokens():
    """The first word ends on the letter the third one opens with. They share
    that character once glued, yet must stay two whole tokens."""
    norm = _normalize("Коли розіграш? Шоб не пропустити")
    assert norm.glued == "колирозіграшобнепропустити"
    assert norm.tokens == ["коли", "розіграш", "шоб", "не", "пропустити"]
    assert match_keywords("Коли розіграш? Шоб не пропустити", ["розіграш"]) == ["розіграш"]


def test_normalize_collapses_runs_and_strips_separators():
    assert _normalize("с.т.р.і.м").glued == "стрім"
    assert _normalize("стрііім").glued == "стрім"
    assert _normalize("с т р і і і м").glued == "стрім"


@pytest.mark.parametrize(
    "latin,cyrillic",
    [
        ("Zagreb", "загреб"), ("Split", "спліт"), ("Rijeka", "рієка"),
        ("Osijek", "осієк"), ("Lviv", "львів"), ("Uzhhorod", "ужгород"),
        ("Dubrovnik", "дубровник"),
    ],
)
def test_latin_place_names_reach_cyrillic_keywords(latin, cyrillic):
    assert match_keywords(f"виїзд {latin} завтра", [cyrillic]) == [cyrillic]


class TestDigitKeywords:
    """Seat counts are the payload of a passenger-transport chat: one count must
    never read as another, and leetspeak folding must stay away from digits."""

    @pytest.mark.parametrize("text", ["є 2 місця", "2 місця вільні", "маю 2 місця"])
    def test_hits(self, text):
        assert match_keywords(text, ["2 місця"]) == ["2 місця"]

    @pytest.mark.parametrize("text", ["є 3 місця", "є 12 місць", "22 місця"])
    def test_other_counts_do_not_hit(self, text):
        assert match_keywords(text, ["2 місця"]) == []

    def test_leading_digit_is_not_a_letter(self):
        assert match_keywords("21 місце", ["1 місце"]) == []
        assert match_keywords("1 місце вільне", ["1 місце"]) == ["1 місце"]

    def test_digits_are_never_stretched(self):
        assert _normalize("22 місця", keep_digits=True).glued == "22місця"

    @pytest.mark.parametrize(
        "keyword,text",
        [("20", "в 2020 році"), ("20", "дата 2020"), ("5", "50 грн"), ("2", "25 місць")],
    )
    def test_a_count_does_not_match_a_longer_number(self, keyword, text):
        assert match_keywords(text, [keyword]) == []

    @pytest.mark.parametrize("text", ["рівно 20", "20, не більше", "20 тонн"])
    def test_a_count_matches_a_whole_number(self, text):
        assert match_keywords(text, ["20"]) == ["20"]

    def test_a_count_followed_by_letters_still_matches(self):
        assert match_keywords("1 місцем", ["1 місце"]) == ["1 місце"]


def test_merge_min_len_is_configurable():
    assert match_keywords("контакт", ["акт"], merge_min_len=3) == ["акт"]
    assert match_keywords("контакт", ["акт"], merge_min_len=5) == []


def test_transliterate_leaves_scattered_homoglyphs_alone():
    assert _transliterate("cтpiм") == "cтpiм"
    assert _transliterate("strim") == "стрім"


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("стрім", "стрім", 0),
        ("стірм", "стрім", 1),
        ("стqрім", "стрім", 1),
        ("стрім", "стріми", 1),
        ("грім", "стрім", 2),
        ("трім", "стрім", 1),
        ("абв", "югж", 3),
    ],
)
def test_osa_distance(a, b, expected):
    assert _osa(a, b, 3) == expected


def test_osa_gives_up_past_the_budget():
    assert _osa("зовсім", "стрім", 1) == 2


@pytest.mark.parametrize(
    "a,b,max_d",
    [("ab", "bca", 1), ("ac", "cba", 1), ("сонце", "світанок", 1), ("абв", "югжкл", 2)],
)
def test_osa_over_budget_always_returns_exactly_max_d_plus_one(a, b, max_d):
    """The row-min cut-off is a lower bound, not the answer: without clamping,
    the final cell leaks a true distance and the sentinel loses its meaning."""
    assert _osa(a, b, max_d) == max_d + 1


def test_osa_never_reports_a_reachable_distance_as_unreachable():
    import itertools

    def reference(a, b):
        d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
        for i in range(len(a) + 1):
            d[i][0] = i
        for j in range(len(b) + 1):
            d[0][j] = j
        for i in range(1, len(a) + 1):
            for j in range(1, len(b) + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
                if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                    d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
        return d[len(a)][len(b)]

    for la in range(1, 5):
        for lb in range(1, 5):
            for a in itertools.product("abc", repeat=la):
                for b in itertools.product("abc", repeat=lb):
                    a, b = "".join(a), "".join(b)
                    for max_d in (1, 2):
                        true_d = reference(a, b)
                        expected = true_d if true_d <= max_d else max_d + 1
                        assert _osa(a, b, max_d) == expected, (a, b, max_d)
