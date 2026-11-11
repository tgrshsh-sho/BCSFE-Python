"""The cat picker's query filter, its rows on the fixture save, the level
input parser and the small fixes around them (in-process, no terminal)."""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bcsfe
from bcsfe import core
from bcsfe.cli import cat_picker, color, dialog_creator, tui
from bcsfe.cli.cat_picker import CatFilter, make_info
from bcsfe.cli.edits import cat_editor, storage
from bcsfe.cli.tui import matching
from tests.conftest import REPO, Sandbox

LOCALES = REPO / "src" / "bcsfe" / "files" / "locales"
RARITY_NAMES = ["基本キャラ", "EX", "レア", "激レア", "超激レア", "伝説レア"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def read_properties(path: Path) -> dict[str, str]:
    """One-line ``key=value`` entries (all the fork's cats keys are one line)."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(("#", ">")) and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def fake_tr(locale: str) -> Any:
    values = read_properties(LOCALES / locale / "fork" / "cats.properties")

    def tr(key: str, **kwargs: Any) -> str:
        value = values.get(key, key)
        for name, arg in kwargs.items():
            value = value.replace("{" + name + "}", str(arg))
        return value

    return tr


# -- the filter on hand-made rows --------------------------------------------------

# id, names, readings, rarity, obtainable, gacha, version, owned
ROWS: list[
    tuple[int, list[str], list[str], int, bool | None, bool | None, Any, bool]
] = [
    (0, ["ネコ", "ネコビルダー"], [], 0, True, False, (0, 0, 0), True),
    (1, ["タンクネコ", "ネコカベ"], ["タンクネコ"], 0, True, False, (0, 0, 0), False),
    (
        2,
        ["監獄ネコ", "脱獄ネコ"],
        ["カンゴクネコ", "ダツゴクネコ"],
        1,
        True,
        False,
        (16, 0, 0),
        False,
    ),
    (
        3,
        ["悠久の冒険者エターニス"],
        ["ユウキュウノボウケンシャエターニス"],
        4,
        True,
        True,
        (16, 0, 0),
        False,
    ),
    (4, ["5_1", "5_2"], [], 4, False, True, (16, 0, 0), False),
    (5, ["Mr.", "Super Mr."], ["ミスター"], 1, True, False, (6, 6, 0), True),
    (6, ["ネコＵＦＯ"], [], 2, True, True, (16, 10, 0), True),
    (7, ["ﾈｺｻﾞﾙ"], [], 3, False, True, (16, 1, 0), False),
    (8, ["キモネコ"], [], -1, None, None, None, False),
    (9, ["ネコジャラミ"], [], 5, True, True, (10, 4, 0), True),
]


@pytest.fixture
def flt(monkeypatch: pytest.MonkeyPatch) -> CatFilter:
    monkeypatch.setattr(cat_picker, "tr", fake_tr("ja"))
    infos = [
        make_info(i, names, readings, rarity, obtainable, gacha, version)
        for i, names, readings, rarity, obtainable, gacha, version, _ in ROWS
    ]
    owned = [row[-1] for row in ROWS]
    return CatFilter(infos, owned, cat_picker.facet_words(RARITY_NAMES))


def test_empty_query_lists_every_cat_in_id_order(flt: CatFilter) -> None:
    assert flt("") == list(range(10))
    assert flt("   ") == list(range(10))


def test_ids_and_ranges_in_the_order_typed(flt: CatFilter) -> None:
    assert flt("3") == [3]
    assert flt("2-4") == [2, 3, 4]
    assert flt("4-2") == [4, 3, 2]
    assert flt("7 2 5") == [7, 2, 5]
    assert flt("2-4 3 0") == [2, 3, 4, 0]  # no duplicates
    assert flt("8-100") == [8, 9]  # clamped to the existing ids
    assert flt("99 100-120") == []


def test_id_ranges_typed_with_an_ime(flt: CatFilter) -> None:
    assert flt("2〜4") == [2, 3, 4]
    assert flt("2~4") == [2, 3, 4]
    assert flt("２－４") == [2, 3, 4]  # full-width digits and hyphen
    assert flt("２〜４") == [2, 3, 4]
    assert flt("２ｰ４") == [2, 3, 4]  # half-width long vowel mark
    assert flt("１、３,５") == [1, 3, 5]
    assert flt("　３　") == [3]  # full-width spaces


def test_ids_mixed_with_other_terms_are_anded(flt: CatFilter) -> None:
    assert flt("0-9 所持") == [0, 5, 6, 9]
    assert flt("9-0 所持") == [9, 6, 5, 0]  # still in the order typed
    assert flt("0-3 ねこ") == [0, 1, 2]
    assert flt("3 所持") == []


def test_owned_facet(flt: CatFilter) -> None:
    assert flt("所持") == [0, 5, 6, 9]
    assert flt("未所持") == [1, 2, 3, 4, 7, 8]
    assert flt("入手済み") == flt("所持")  # alias from the locale
    assert flt("未入手") == flt("未所持")
    assert flt("所持 未所持") == list(range(10))  # same facet: OR


def test_obtainable_and_gacha_facets(flt: CatFilter) -> None:
    assert flt("入手可能") == [0, 1, 2, 3, 5, 6, 9]
    assert flt("入手不可") == [4, 7]  # unknown (8) matches neither
    assert flt("ガチャ") == [3, 4, 6, 7, 9]
    assert flt("ガチャ以外") == [0, 1, 2, 5]
    assert flt("ｶﾞﾁｬ以外") == [0, 1, 2, 5]


def test_rarity_facet_is_exact_not_substring(flt: CatFilter) -> None:
    assert flt("レア") == [6]
    assert flt("激レア") == [7]
    assert flt("超激レア") == [3, 4]
    assert flt("伝説レア") == [9]
    assert flt("EX") == [2, 5]
    assert flt("ex") == [2, 5]
    assert flt("基本キャラ") == [0, 1]
    assert flt("基本") == [0, 1]  # the game's name without キャラ
    assert flt("超激れあ") == [3, 4]  # kana folding applies to facets too
    assert flt("超激") == []  # a part is a name search, not a facet


def test_facets_combine(flt: CatFilter) -> None:
    assert flt("未所持 超激レア") == [3, 4]
    assert flt("超激レア 未所持") == [3, 4]
    assert flt("レア 激レア 未所持") == [7]  # rarities OR'ed, AND owned
    assert flt("ガチャ 入手可能 所持") == [6, 9]


def test_version_facet_is_a_prefix_of_the_version(flt: CatFilter) -> None:
    assert flt("16.0.0") == [2, 3, 4]
    assert flt("16.00.00") == [2, 3, 4]  # upstream's format
    assert flt("16.0") == [2, 3, 4]
    assert flt("16.1") == [7]  # not 16.10.0
    assert flt("16.") == [2, 3, 4, 6, 7]
    assert flt("16.0 10.4") == [2, 3, 4, 9]  # OR
    assert flt("16.0 未所持 ガチャ以外") == [2]
    assert flt("6") == [6]  # without a dot a number is an id


def test_name_terms_match_any_form_or_reading(flt: CatFilter) -> None:
    assert flt("監獄") == [2]
    assert flt("脱獄") == [2]  # second form
    assert flt("かんごく") == [2]  # reading, hiragana for katakana
    assert flt("ダツゴク") == [2]
    assert flt("えたーにす") == [3]
    assert flt("ねこざる") == [7]  # half-width katakana in the name
    assert flt("ufo") == [6]  # full-width latin in the name
    assert flt("ねこ かべ") == [1]  # every term must match
    assert flt("mr.") == [5]  # a dot alone does not make a version
    assert flt("super") == [5]
    assert flt("みすたー") == [5]
    assert flt("所") == []  # not a facet word: a part of a name


def test_placeholder_names_are_not_searchable(flt: CatFilter) -> None:
    assert flt.infos[4].name == ""
    assert flt("5_1") == []
    assert flt("4") == [4]


def test_english_fallback_words_without_a_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Missing:
        def get_key(self, key: str, **kwargs: object) -> str:
            return key

    monkeypatch.setattr(core.core_data, "local_manager", Missing(), raising=False)
    assert cat_picker.tr("cats_picker_title") == "Select cats:"
    assert cat_picker.tr("cats_selected_more", count=3) == "and 3 more"
    words = cat_picker.facet_words(["Uber Rare"])
    assert words["locked"] == (cat_picker.OWNED, False)
    assert words["non-gacha"] == (cat_picker.GACHA, False)
    assert words["uberrare"] == (cat_picker.RARITY, 0)


def test_fallback_texts_match_the_english_locale() -> None:
    en = read_properties(LOCALES / "en" / "fork" / "cats.properties")
    for key, value in cat_picker.FALLBACK.items():
        assert key in en, key
        assert value == en[key].replace("{{max}}", "max"), key
    assert set(en) == set(cat_picker.FALLBACK)


# -- level input --------------------------------------------------------------------


def test_clean_upgrade_input() -> None:
    clean = cat_picker.clean_upgrade_input
    assert clean("５０＋３０", "max") == "50+30"
    assert clean(" 5〜10 + 20ー30 ", "max") == "5-10+20-30"
    assert clean("最大+MAX", "max") == "max+max"
    assert clean("さいだい+", "max") == "max+"


def upgrade_or_error(text: str) -> object:
    result = core.Upgrade.parse_user_upgrade(text, 49, 70)
    if isinstance(result, str):
        return result
    return (result.base, result.plus, result.base_range, result.plus_range)


def test_parse_user_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    class Locale:
        def get_key(self, key: str, escape: bool = True, **kwargs: object) -> str:
            if key == "max":
                return "max"
            return key + "".join(f" {k}={v}" for k, v in kwargs.items())

    monkeypatch.setattr(core.core_data, "local_manager", Locale(), raising=False)
    assert upgrade_or_error("50+30") == (49, 30, (49, 49), (30, 30))
    assert upgrade_or_error("max+max") == (49, 70, (49, 49), (70, 70))
    assert upgrade_or_error("50+") == (49, -1, (49, 49), (0, 0))
    assert upgrade_or_error("+20") == (-1, 20, (0, 0), (20, 20))
    assert upgrade_or_error("5-10+20-30") == (6, 25, (4, 9), (20, 30))
    assert upgrade_or_error("90+100") == (89, 100, (49, 49), (70, 70))  # capped
    assert upgrade_or_error("abc") == "invalid_upgrade_base base=abc"
    assert upgrade_or_error("5-x") == "invalid_upgrade_base_random min=5 max=x"
    assert upgrade_or_error("+x") == "invalid_upgrade_plus plus=x"
    assert upgrade_or_error("+1-y") == "invalid_upgrade_plus_random min=1 max=y"


def test_classic_upgrade_prompt_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Locale:
        def get_key(self, key: str, escape: bool = True, **kwargs: object) -> str:
            return {"max": "max", "quit_key": "q"}.get(key, key)

    class Config:
        def get_bool(self, key: object) -> bool:
            return False

    monkeypatch.setattr(core.core_data, "local_manager", Locale(), raising=False)
    monkeypatch.setattr(core.core_data, "config", Config(), raising=False)
    monkeypatch.setattr(color, "colorize", matching.strip_markup)
    monkeypatch.setattr(tui, "is_interactive", lambda: False)
    answers = iter(["abc", "q", "10+5"])
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    assert core.Upgrade.get_user_upgrade(49, 70) == (None, False)
    assert core.Upgrade.get_user_upgrade(49, 70, 3) == (None, True)
    upgrade, should_exit = core.Upgrade.get_user_upgrade(49, 70)
    assert upgrade is not None and (upgrade.base, upgrade.plus) == (9, 5)
    assert not should_exit
    assert prompts == ["upgrade_input"] * 3
    out = capsys.readouterr().out
    assert out.count("max_upgrade") == 3
    assert "invalid_upgrade_base" in out


# -- the fixture save -------------------------------------------------------------


@pytest.fixture
def save(
    editor_sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> Iterator[core.SaveFile]:
    """The fixture save loaded in this process with ``locale: ja``; every
    global the editor sets up is restored afterwards."""
    root = editor_sandbox.root
    for key, value in editor_sandbox.env.items():
        monkeypatch.setenv(key, value)
    (root / "home").mkdir(exist_ok=True)  # conftest's sandbox_env made it
    monkeypatch.setenv("HOME", str(root / "home"))
    editor_sandbox.write_config(locale="ja")
    data = root / "data"
    shutil.copytree(Path(bcsfe.__file__).parent / "files", data, dirs_exist_ok=True)
    monkeypatch.setattr(core, "config_path", core.Path(str(editor_sandbox.config_path)))
    monkeypatch.setattr(core, "data_dir_path", core.Path(str(data)))
    monkeypatch.setattr(core, "log_path", core.Path(str(root / "bcsfe.log")))
    monkeypatch.setattr(
        core, "game_data_path", core.Path(str(editor_sandbox.game_data))
    )
    monkeypatch.setattr(core, "core_data", core.CoreData())
    monkeypatch.setattr(cat_picker, "_cache", None)
    core.core_data.init_data()
    yield core.SaveFile(core.Data.from_file(core.Path(str(root / "SAVE_DATA"))))


def test_rows_built_from_the_fixture_save_quickly(save: core.SaveFile) -> None:
    start = time.perf_counter()
    picker = cat_picker.CatPicker(save)
    first = time.perf_counter() - start
    start = time.perf_counter()
    again = cat_picker.CatPicker(save)
    second = time.perf_counter() - start
    assert first < 1.0, f"building the rows took {first:.2f} s"
    assert second < 0.2, f"rows are not cached: {second:.2f} s"
    assert again.infos is picker.infos
    assert len(picker.choices) == len(save.cats.cats) == 888

    rows = {
        i: (c.label, " ".join(c.hint.split())) for i, c in enumerate(picker.choices)
    }
    assert rows[0] == ("  0 ネコ", "基本キャラ Lv20+0")
    assert rows[1] == ("  1 タンクネコ", "基本キャラ Lv20+90")
    assert rows[886] == ("886 悠久の冒険者エターニス", "超激レア 未所持")
    assert rows[887] == ("887 (名前なし)", "超激レア 未所持")  # "888_1"
    assert rows[500] == ("500 (名前なし)", "EX 未所持")  # "501_1"
    # hints are padded to columns
    assert len({cat_picker.width(c.hint) for c in picker.choices}) == 1
    assert picker.footer() == (
        "例: <@t>未所持 超激レア</> / <@t>16.0.0</> / <@t>882-886</> / 名前の一部"
    )
    assert (
        save.cats.cats[886].names
        == ["悠久の冒険者エターニス"] + ["真理の探究者エターニス"] * 2
    )


def test_rarity_names_of_the_fixture_game_data(save: core.SaveFile) -> None:
    assert save.cats.get_rarity_names(save) == RARITY_NAMES


def test_filter_agrees_with_upstream_lists(save: core.SaveFile) -> None:
    picker = cat_picker.CatPicker(save)
    flt = picker.filter
    cats = save.cats

    def ids(query: str) -> list[int]:
        return [cats.cats[i].id for i in flt(query)]

    def upstream(found: list[core.Cat] | None) -> list[int]:
        return sorted(cat.id for cat in found or [])

    assert ids("882-886") == [882, 883, 884, 885, 886]
    assert ids("かんごく") == [882, 883, 884, 885]
    assert ids("16.0.0") == [882, 883, 884, 885, 886, 887]
    assert ids("所持") == upstream(cats.get_unlocked_cats())
    assert ids("未所持") == upstream(cats.get_non_unlocked_cats())
    assert ids("入手可能") == upstream(cats.get_cats_obtainable(save))
    assert ids("入手不可") == upstream(cats.get_cats_non_obtainable(save))
    assert ids("ガチャ以外") == upstream(cats.get_non_gacha_cats(save))
    for rarity, name in enumerate(RARITY_NAMES):
        assert ids(name) == upstream(cats.get_cats_rarity(save, rarity)), name
    unowned_uber = [c.id for c in cats.get_cats_rarity(save, 4) if not c.unlocked]
    assert ids("未所持 超激レア") == unowned_uber
    assert 0 < len(unowned_uber) < 888


def test_summary_fits_one_line(
    save: core.SaveFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    cats = save.cats.cats
    monkeypatch.setattr(
        cat_picker.shutil, "get_terminal_size", lambda *_: os.terminal_size((100, 30))
    )
    line = cat_picker.summary(save, cats[882:887])
    assert line == (
        "選択中のキャラ (<@t>5</> 体): 882 監獄ネコ \\/ 883 監獄ネコガール"
        " \\/ 884 監獄タンクネコ ほか 2 体"
    )
    assert cat_picker.width(cat_picker.plain(line)) <= 99
    assert (
        cat_picker.summary(save, [cats[887]])
        == "選択中のキャラ (<@t>1</> 体): 887 (名前なし)"
    )
    assert cat_picker.summary(save, []) == "キャラが選択されていません"
    many = cat_picker.plain(cat_picker.summary(save, cats))
    assert many.startswith("選択中のキャラ (888 体): 0 ネコ / 1 タンクネコ")
    assert many.endswith(" 体") and cat_picker.width(many) <= 99


def test_interactive_selection_summary_replaces_one_line_per_cat(
    save: core.SaveFile,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    editor = cat_editor.CatEditor(save)
    cats = save.cats.cats[882:887]
    monkeypatch.setattr(tui, "is_interactive", lambda: True)
    editor.print_selected_cats(cats)
    out = ANSI.sub("", capsys.readouterr().out)
    assert len(out.splitlines()) == 1 and "882 監獄ネコ" in out
    monkeypatch.setattr(tui, "is_interactive", lambda: False)
    editor.print_selected_cats(cats)
    out = ANSI.sub("", capsys.readouterr().out)
    assert len(out.splitlines()) == 5 and "監獄ネコ (882) を選択中" in out


def test_action_questions_offer_the_safe_option_first(
    save: core.SaveFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    firsts: list[str] = []

    def capture(actions: dialog_creator.Actions[Any], dialog: str, **_: Any) -> None:
        firsts.append(actions.actions[0].options[0])

    monkeypatch.setattr(dialog_creator, "single_select_key", capture)
    editor = cat_editor.CatEditor(save)
    cats = save.cats.cats[882:884]
    Editor = cat_editor.CatEditor
    Editor.unlock_remove_cats_run(save, cats, editor)
    Editor.true_form_remove_form_cats_run(save, cats, editor)
    Editor.fourth_form_remove_form_cats_run(save, cats, editor)
    Editor.upgrade_talents_remove_talents_cats_run(save, cats, editor)
    Editor.unlock_cat_guide_remove_guide_run(save, cats, editor)
    assert firsts == [
        "キャラを入手",
        "第3形態に進化",
        "第4形態に進化",
        "本能を強化",
        "にゃんこ図鑑に登録",
    ]


def test_gacha_banner_names_show_their_ids(
    save: core.SaveFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Infos:
        def __init__(self, save_file: core.SaveFile) -> None:
            pass

        def get_all_names(self) -> dict[int, str]:
            return {12: "超ネコ祭", 3: "極ネコ祭"}

    shown: list[list[str]] = []

    def capture(options: list[str], dialog: str, **_: Any) -> list[int]:
        shown.append(options)
        return [1]

    monkeypatch.setattr(core, "GatyaInfos", Infos)
    monkeypatch.setattr(dialog_creator, "yes_no_key", lambda *a, **k: False)
    monkeypatch.setattr(dialog_creator, "multi_select_indexes_key", capture)
    editor = cat_editor.CatEditor(save)
    assert editor.select_gatya_banner_name() == [12]
    assert shown == [["極ネコ祭 (<@s>3</>)", "超ネコ祭 (<@s>12</>)"]]


def test_storage_removal_dialog_has_a_real_title(
    save: core.SaveFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = save.cats.storage_items
    if not items:
        pytest.skip("this save has no storage slots")
    for item in items:
        item.item_type = item.item_id = 0
    storage.add_item(items, core.StorageItem.from_cat(882))
    storage.add_item(items, core.StorageItem.from_cat(883))
    titles: list[str] = []

    def capture(options: list[str], dialog: str) -> list[int] | None:
        titles.append(dialog)
        return None

    monkeypatch.setattr(dialog_creator, "multi_select_indexes_raw", capture)
    storage.remove_items(save)
    assert titles == ["削除するキャラ・能力を選択してください:"]
