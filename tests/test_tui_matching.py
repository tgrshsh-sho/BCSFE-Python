"""Query normalisation and filtering (bcsfe.cli.tui.matching)."""

from __future__ import annotations

import time

import pytest

from bcsfe.cli.tui.matching import Matcher, normalize, split_query, strip_markup


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ネコ", "ねこ"),
        ("ﾈｺ", "ねこ"),  # half-width katakana
        ("ｶﾞﾏﾄﾄ", "がまとと"),  # half-width with dakuten
        ("ヴ", "ゔ"),
        ("ヽヾ", "ゝゞ"),
        ("１２３", "123"),  # full-width digits
        ("ＸＰ", "xp"),  # full-width letters + casefold
        ("Straße", "strasse"),  # casefold, not just lower
        ("XP", "xp"),
    ],
)
def test_normalize_folds(a: str, b: str) -> None:
    assert normalize(a) == normalize(b)


def test_normalize_keeps_distinct_text_distinct() -> None:
    assert normalize("ネコ") != normalize("イヌ")
    assert normalize("ー") == "ー"  # prolonged sound mark is not a kana


def test_split_query_handles_full_width_space() -> None:
    assert split_query("ネコ　缶  XP") == ["ねこ", "缶", "xp"]
    assert split_query("   ") == []


def test_strip_markup() -> None:
    assert strip_markup("<@t>赤</>マタタビ : <@q>154</>") == "赤マタタビ : 154"
    assert strip_markup(r"a \<num\>-\<num\> \/ b") == "a <num>-<num> / b"
    assert strip_markup("<#ff0000>red</> plain") == "red plain"
    assert strip_markup("no tags") == "no tags"


LABELS = [
    "おかしなネコ缶",  # 0  contains ねこ
    "ネコ缶",  # 1  starts with ねこ
    "XP",  # 2
    "ネコビタンA",  # 3  starts with ねこ
    "<@q>ねこ</>じゃらし",  # 4  starts with ねこ (markup stripped)
    "イヌ",  # 5  matched only through its keywords
]
KEYWORDS = ["", "cat food", "experience", "catamin a", "", "ネコ dog"]


def test_empty_query_keeps_everything_in_order() -> None:
    matcher = Matcher(LABELS, KEYWORDS)
    assert matcher.filter("") == [0, 1, 2, 3, 4, 5]
    assert matcher.filter("  ") == [0, 1, 2, 3, 4, 5]


def test_hiragana_finds_katakana_prefix_first_then_stable() -> None:
    matcher = Matcher(LABELS, KEYWORDS)
    # prefix matches (1, 3, 4) in original order, then the others (0, 5)
    assert matcher.filter("ねこ") == [1, 3, 4, 0, 5]
    assert matcher.filter("ネコ") == [1, 3, 4, 0, 5]
    assert matcher.filter("ﾈｺ") == [1, 3, 4, 0, 5]


def test_terms_are_anded_and_keywords_match() -> None:
    matcher = Matcher(LABELS, KEYWORDS)
    assert matcher.filter("ねこ 缶") == [1, 0]
    assert matcher.filter("cat food") == [1]
    assert matcher.filter("CATAMIN") == [3]
    assert matcher.filter("dog") == [5]
    assert matcher.filter("ねこ zzz") == []


def test_multi_term_query_orders_whole_prefix_then_first_term_prefix() -> None:
    matcher = Matcher(["b a x", "a b", "a x b", "x a b"])
    # "a b" whole prefix first, then labels starting with "a", then others
    assert matcher.filter("a b") == [1, 2, 0, 3]


def test_full_width_digits_find_half_width() -> None:
    matcher = Matcher(["アイテム 12", "アイテム 3"])
    assert matcher.filter("１２") == [0]


def test_markup_is_not_matched() -> None:
    matcher = Matcher(["<@t>赤</>"])
    assert matcher.filter("@t") == []
    assert matcher.filter("赤") == [0]


def test_filter_is_fast_for_large_lists() -> None:
    labels = [f"アイテム {i:05d} ネコ" for i in range(20000)]
    keywords = [f"item {i}" for i in range(20000)]
    start = time.perf_counter()
    matcher = Matcher(labels, keywords)
    build = time.perf_counter() - start
    start = time.perf_counter()
    for query in ("ね", "ねこ", "item 1", "19999", "あいてむ ねこ 0"):
        matcher.filter(query)
    per_query = (time.perf_counter() - start) / 5
    assert matcher.filter("19999") == [19999]
    assert build < 1.0
    assert per_query < 0.1, per_query
