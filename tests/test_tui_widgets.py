"""Widget logic driven through prompt_toolkit's pipe input and a dummy output."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from bcsfe.cli import tui
from bcsfe.cli.tui import _apps, base, classic, strings, widgets
from bcsfe.cli.tui.base import Choice

T = TypeVar("T")

UP = "\x1b[A"
DOWN = "\x1b[B"
LEFT = "\x1b[D"
RIGHT = "\x1b[C"
PAGE_DOWN = "\x1b[6~"
HOME = "\x1b[H"
END = "\x1b[F"
ENTER = "\r"
ESC = "\x1b"
TAB = "\t"
CTRL_A = "\x01"
CTRL_C = "\x03"
CTRL_D = "\x04"
CTRL_N = "\x0e"
CTRL_P = "\x10"
CTRL_U = "\x15"
BACKSPACE = "\x7f"

ITEMS = [
    Choice("ネコ缶", "cat food"),
    Choice("XP", "experience"),
    Choice("ネコビタンA", "catamin a"),
    Choice("おかしなネコ缶"),
    Choice("<@q>赤</>マタタビ", hint="154"),
]


def drive(func: Callable[[], T], *keys: str) -> T:
    """Run ``func`` with the given keys already typed."""
    with create_pipe_input() as pipe:
        for key in keys:
            pipe.send_text(key)
        with create_app_session(input=pipe, output=DummyOutput()):
            return func()


def select(*keys: str, default: int = 0, items: list[Choice] = ITEMS) -> int | None:
    return drive(
        lambda: _apps.select("title", items, default=default, footer=None), *keys
    )


def multi(*keys: str, selected: tuple[int, ...] = ()) -> list[int] | None:
    return drive(
        lambda: _apps.multi_select("title", ITEMS, selected=selected, footer=None),
        *keys,
    )


# -- select ------------------------------------------------------------------------


def test_select_enter_picks_highlighted() -> None:
    assert select(ENTER) == 0
    assert select(DOWN, DOWN, ENTER) == 2
    assert select(DOWN, CTRL_N, CTRL_P, ENTER) == 1
    assert select(ENTER, default=3) == 3


def test_select_movement_is_clamped() -> None:
    assert select(UP, UP, ENTER) == 0
    assert select(END, ENTER) == 4
    assert select(END, HOME, ENTER) == 0
    assert select(PAGE_DOWN, PAGE_DOWN, ENTER) == 4
    assert select(DOWN * 10, ENTER) == 4


def test_select_filter_with_hiragana_returns_original_index() -> None:
    # ねこ: prefix matches ネコ缶 (0), ネコビタンA (2), then おかしなネコ缶 (3)
    assert select("ねこ", ENTER) == 0
    assert select("ねこ", DOWN, ENTER) == 2
    assert select("ねこ", DOWN, DOWN, ENTER) == 3
    assert select("catamin", ENTER) == 2
    assert select("赤", ENTER) == 4


def test_select_query_change_resets_cursor() -> None:
    assert select(DOWN, DOWN, "x", ENTER) == 1
    assert select("ねこ", DOWN, BACKSPACE, BACKSPACE, ENTER) == 0


def test_select_enter_without_matches_does_nothing() -> None:
    # Enter on an empty result is ignored; Esc then cancels
    assert select("zzz", ENTER, ESC) is None
    assert select("zzz", ENTER, CTRL_U, ENTER) == 0


def test_select_cancel() -> None:
    assert select(ESC) is None
    assert select(DOWN, CTRL_C) is None


def test_select_paste_goes_to_query() -> None:
    assert select("\x1b[200~ネコビ\r\n\x1b[201~", ENTER) == 2


def test_select_many_rows_renders_only_visible() -> None:
    items = [Choice(f"アイテム {i}") for i in range(5000)]
    assert select(END, ENTER, items=items) == 4999
    assert select("4321", ENTER, items=items) == 4321

    state = _apps.ListState(items, multi=False)
    rendered: list[int] = []
    original = state.row

    def counting_row(position: int, width: int):  # type: ignore[no-untyped-def]
        rendered.append(position)
        return original(position, width)

    state.row = counting_row  # type: ignore[method-assign]
    content = _apps.ListControl(state).create_content(80, 15)
    assert content.line_count == 15
    assert len(rendered) == 15


def test_list_state_scrolls_to_keep_cursor_visible() -> None:
    state = _apps.ListState([Choice(str(i)) for i in range(100)], multi=False)
    state.move(40)
    state.scroll(15)
    assert state.top <= state.cursor < state.top + 15
    state.move_to(0)
    state.scroll(15)
    assert state.top == 0


# -- multi_select ---------------------------------------------------------------


def test_multi_enter_without_checks_returns_highlighted() -> None:
    assert multi(ENTER) == [0]
    assert multi(DOWN, ENTER) == [1]


def test_multi_tab_toggles_and_moves_down() -> None:
    assert multi(TAB, TAB, ENTER) == [0, 1]
    assert multi(TAB, UP, TAB, ENTER) == [1]  # second Tab unchecks row 0
    assert multi(DOWN, TAB, ENTER) == [1]


def test_multi_selection_survives_query_changes() -> None:
    # check ネコ缶 via a query, then XP via another query
    assert multi("ねこ", TAB, CTRL_U, "xp", TAB, CTRL_U, ENTER) == [0, 1]


def test_multi_ctrl_a_checks_visible_and_ctrl_d_clears() -> None:
    assert multi("ねこ", CTRL_A, ENTER) == [0, 2, 3]
    assert multi(CTRL_A, ENTER) == [0, 1, 2, 3, 4]
    assert multi(CTRL_A, CTRL_D, DOWN, ENTER) == [1]


def test_multi_initial_selection_and_cancel() -> None:
    assert multi(ENTER, selected=(4, 1)) == [1, 4]
    assert multi(TAB, ESC, selected=(1,)) is None


def test_multi_result_is_sorted() -> None:
    assert multi(END, TAB, HOME, TAB, ENTER) == [0, 4]


# -- confirm ------------------------------------------------------------------------


def confirm(*keys: str, default: bool = False) -> bool | None:
    return drive(lambda: _apps.confirm("Continue?", default=default), *keys)


def test_confirm_single_key_answers() -> None:
    assert confirm("y") is True
    assert confirm("Y") is True
    assert confirm("ｙ") is True  # full-width y from an IME
    assert confirm("n", default=True) is False
    assert confirm("x", "n") is False  # other keys are ignored


def test_confirm_enter_accepts_highlighted_default() -> None:
    assert confirm(ENTER) is False
    assert confirm(ENTER, default=True) is True


def test_confirm_switching() -> None:
    assert confirm(LEFT, ENTER) is True
    assert confirm(RIGHT, ENTER) is True
    assert confirm(TAB, TAB, ENTER) is False


def test_confirm_cancel() -> None:
    assert confirm(ESC) is None
    assert confirm(CTRL_C, default=True) is None
    assert confirm("q") is None  # the classic quit key


# -- text / integer -----------------------------------------------------------------


def text(*keys: str, default: str = "", validate=None) -> str | None:  # type: ignore[no-untyped-def]
    return drive(lambda: _apps.text("Name:", default=default, validate=validate), *keys)


def test_text_returns_typed_text() -> None:
    assert text("ネコ 缶", ENTER) == "ネコ 缶"
    assert text(ENTER) == ""
    assert text(ENTER, default="abc") == "abc"
    assert text("d", ENTER, default="abc") == "abcd"
    assert text(ESC, default="abc") is None


def test_text_validation_keeps_input() -> None:
    def no_digits(value: str) -> str | None:
        return "no digits" if any(c.isdigit() for c in value) else None

    assert text("ab1", ENTER, BACKSPACE, ENTER, validate=no_digits) == "ab"


def integer(*keys: str, **kwargs: object) -> int | None:
    params: dict[str, object] = {
        "minimum": 0,
        "maximum": 100,
        "current": None,
        "clamp": False,
    }
    params.update(kwargs)
    return drive(lambda: _apps.integer("Value:", **params), *keys)  # type: ignore[arg-type]


def test_integer_parses_numbers() -> None:
    assert integer("42", ENTER) == 42
    assert integer("１２", ENTER) == 12  # full-width digits
    assert integer("1,0", ENTER) == 10
    assert integer(" 7 ", ENTER) == 7


def test_integer_rejects_and_keeps_input() -> None:
    # "abc" is rejected; the field keeps it, so 3 backspaces are needed
    assert integer("abc", ENTER, BACKSPACE * 3, "5", ENTER) == 5
    # out of range is rejected without clamp
    assert integer("101", ENTER, BACKSPACE, BACKSPACE, ENTER) == 1
    assert integer("-1", ENTER, CTRL_U, "0", ENTER) == 0


def test_integer_clamps() -> None:
    assert integer("500", ENTER, clamp=True) == 100
    assert integer("-5", ENTER, clamp=True) == 0
    assert integer("15", ENTER, minimum=20, clamp=True) == 20


def test_integer_empty_enter() -> None:
    assert integer(ENTER, current=33) == 33
    # without a current value empty Enter is an error
    assert integer(ENTER, "9", ENTER) == 9
    assert integer(ESC, current=33) is None
    assert integer("q", ENTER, current=33) is None  # the classic quit key


def test_parse_int() -> None:
    assert _apps.parse_int("１，２３４") == 1234
    assert _apps.parse_int("1_000") == 1000
    assert _apps.parse_int("-12") == -12
    assert _apps.parse_int("1e3") is None
    assert _apps.parse_int("") is None


# -- helpers ----------------------------------------------------------------------


def test_truncate_respects_wide_characters() -> None:
    frags = [("", "ネコ缶ABC")]  # width 6 + 3
    assert _apps.width_of(_apps.truncate(frags, 9)) == 9
    cut = _apps.truncate(frags, 6)
    assert _apps.width_of(cut) <= 6
    assert "".join(t for _, t in cut).endswith("…")
    ascii_cut = _apps.truncate(frags, 6, "...")
    assert "".join(t for _, t in ascii_cut) == "ネ..."


def test_ascii_glyphs_when_output_cannot_encode_unicode() -> None:
    class EucOutput(DummyOutput):
        def encoding(self) -> str:
            return "euc_jp"

    with create_app_session(output=DummyOutput()):
        assert _apps.glyphs() is _apps.UNICODE_GLYPHS
    with create_app_session(output=EucOutput()):
        assert _apps.glyphs() is _apps.ASCII_GLYPHS
        row = _apps.ListState([Choice("ネコ")], multi=False).row(0, 20)
        assert "".join(item[1] for item in row).startswith("> ネコ")


def test_row_right_aligns_hint_with_wide_labels() -> None:
    state = _apps.ListState([Choice("赤マタタビ", hint="154")], multi=True)
    row = state.row(0, 40)
    assert _apps.width_of(row) == 40
    text_row = "".join(item[1] for item in row)
    assert text_row.rstrip().endswith("154")


def test_summary_text_for_many_items() -> None:
    state = _apps.ListState([Choice(f"<@t>item{i}</>") for i in range(6)], multi=True)
    assert _apps._list_summary(state, [0, 1]) == "item0 / item1"  # pyright: ignore[reportPrivateUsage]
    many = _apps._list_summary(state, [0, 1, 2, 3, 4])  # pyright: ignore[reportPrivateUsage]
    assert many is not None and many.startswith("5") and "item0 / item1 / item2" in many
    assert "item3" not in many


# -- public API, fallback and strings ---------------------------------------------


def test_public_api_uses_classic_prompt_when_not_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(widgets, "is_interactive", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(classic, "select", lambda *a: calls.append("select") or 1)  # pyright: ignore
    assert tui.select("t", ["a", "b"]) == 1
    assert calls == ["select"]
    with pytest.raises(tui.TuiUnavailable):
        tui.select("t", ["a", "b"], fallback=False)
    assert tui.select("t", []) is None
    assert tui.multi_select("t", []) == []


def test_public_api_runs_widget_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(widgets, "is_interactive", lambda: True)
    assert drive(lambda: tui.select("t", ["a", "ねこ"]), "ネコ", ENTER) == 1
    assert drive(lambda: tui.multi_select("t", ["a", "b"]), TAB, TAB, ENTER) == [0, 1]
    assert drive(lambda: tui.confirm("q?"), "y") is True
    assert drive(lambda: tui.text("t"), "abc", ENTER) == "abc"
    assert drive(lambda: tui.integer("t", minimum=0, maximum=9), "7", ENTER) == 7


def test_widget_failure_falls_back_to_classic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(widgets, "is_interactive", lambda: True)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(_apps, "select", broken)
    monkeypatch.setattr(classic, "select", lambda *a: 0)  # pyright: ignore
    monkeypatch.setattr(widgets, "_notice_shown", True)
    assert tui.select("t", ["a", "b"]) == 0
    with pytest.raises(tui.TuiUnavailable):
        tui.select("t", ["a", "b"], fallback=False)


def test_exception_in_key_handler_ends_the_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(self: _apps.ListState, delta: int) -> None:
        raise ValueError("bad handler")

    monkeypatch.setattr(_apps.ListState, "move", explode)
    with pytest.raises(ValueError, match="bad handler"):
        select(DOWN)


def test_is_interactive_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(base.sys, "stdin", Tty())
    monkeypatch.setattr(base.sys, "stdout", Tty())
    monkeypatch.setattr(base, "_config_ui_mode", lambda: "auto")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("BCSFE_UI", raising=False)
    assert base.is_interactive() is True
    monkeypatch.setenv("BCSFE_UI", "Classic")
    assert base.is_interactive() is False
    monkeypatch.setenv("BCSFE_UI", "auto")
    monkeypatch.setattr(base, "_config_ui_mode", lambda: "classic")
    assert base.is_interactive() is False
    monkeypatch.setattr(base, "_config_ui_mode", lambda: "auto")
    monkeypatch.setenv("TERM", "dumb")
    assert base.is_interactive() is False


def test_is_interactive_false_without_tty() -> None:
    # pytest captures stdout, so this process is never interactive
    assert base.is_interactive() is False


def test_tr_falls_back_to_english_when_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Missing:
        def get_key(self, key: str, **kwargs: object) -> str:
            return key

    from bcsfe import core

    monkeypatch.setattr(core.core_data, "local_manager", Missing(), raising=False)
    assert strings.tr("tui_yes") == "Yes"
    assert strings.tr("tui_counter", shown=3, total=10) == "3 / 10"
    assert strings.tr("tui_range", min="<0>", max=5) == r"range \<0\> - 5"
    assert strings.tr("no_such_key") == "no_such_key"


# -- pick: headings, custom filter, extra keys, initial query -------------------------

CTRL_O = "\x0f"

SECTIONS = [
    Choice("■ A", heading=True),
    Choice("a1"),
    Choice("a2"),
    Choice("■ B", heading=True),
    Choice("b1", "beta"),
]
MORE = tui.ExtraKey("more", "c-o", "その他")


def pick(*keys: str, multi: bool = False, **options: object) -> tui.ListResult | None:
    values: dict[str, object] = {
        "multi": multi,
        "default": 0,
        "selected": (),
        "footer": None,
        "filter": None,
        "extra_keys": (),
        "query": "",
    }
    values.update(options)
    return drive(lambda: _apps.pick("title", SECTIONS, **values), *keys)  # pyright: ignore


def picked(*keys: str, **options: object) -> tuple[int, ...] | None:
    result = pick(*keys, **options)
    return None if result is None else result.indexes


def test_pick_cursor_skips_headings() -> None:
    assert picked(ENTER) == (1,)  # starts on the first row that is not a heading
    assert picked(DOWN, DOWN, ENTER) == (4,)
    assert picked(END, UP, ENTER) == (2,)
    assert picked(END, HOME, ENTER) == (1,)
    assert picked(UP, UP, ENTER) == (1,)
    assert picked(PAGE_DOWN, ENTER) == (4,)
    assert picked(ENTER, default=0) == (1,)
    assert picked(ENTER, default=4) == (4,)


def test_pick_query_hides_headings_and_counter_ignores_them() -> None:
    assert picked("beta", ENTER) == (4,)
    state = _apps.ListState(SECTIONS, multi=False)
    assert state.visible == [0, 1, 2, 3, 4]
    assert "".join(text for _, text, *_ in state.counter()).strip() == "3 / 3"
    state.refilter("a")  # a1, a2 and b1 (keyword "beta"), never the headings
    assert state.visible == [1, 2, 4]
    state.refilter("a1")
    assert state.visible == [1]
    assert "".join(text for _, text, *_ in state.counter()).strip() == "1 / 3"


def test_pick_multi_never_checks_headings() -> None:
    assert picked(CTRL_A, ENTER, multi=True) == (1, 2, 4)
    assert picked(ENTER, multi=True, selected=(0, 3, 4)) == (4,)


def test_pick_custom_filter_controls_rows_and_order() -> None:
    def only_x(query: str) -> list[int]:
        return [4, 99, 1, 4] if query == "x" else list(range(len(SECTIONS)))

    assert picked("x", ENTER, filter=only_x) == (4,)
    assert picked("x", DOWN, ENTER, filter=only_x) == (1,)
    assert picked("x", DOWN, DOWN, ENTER, filter=only_x) == (
        1,
    )  # 99 and the repeat are dropped
    assert picked(DOWN, DOWN, ENTER, filter=only_x) == (
        4,
    )  # headings it returns stay unselectable


def test_pick_extra_key_reports_rows_and_query() -> None:
    result = pick(DOWN, CTRL_O, extra_keys=(MORE,))
    assert result == tui.ListResult((2,), "more", "")
    result = pick("beta", CTRL_O, extra_keys=(MORE,))
    assert result == tui.ListResult((4,), "more", "beta")
    result = pick(TAB, CTRL_O, multi=True, extra_keys=(MORE,))
    assert result == tui.ListResult((1,), "more", "")
    result = pick("zzz", CTRL_O, extra_keys=(MORE,))
    assert result == tui.ListResult((), "more", "zzz")
    assert pick(CTRL_O, ESC) is None  # without extra_keys Ctrl-O does nothing


def test_pick_initial_query() -> None:
    assert picked(ENTER, query="beta") == (4,)
    result = pick(ENTER, query="a")
    assert result == tui.ListResult((1,), None, "a")


def test_key_label() -> None:
    assert _apps.key_label("c-o") == "Ctrl-O"
    assert _apps.key_label("f2") == "F2"
    assert _apps.key_label("tab") == "Tab"


def test_pick_without_selectable_rows_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(widgets, "is_interactive", lambda: True)
    assert tui.pick("t", [Choice("■", heading=True)]) is None
    assert tui.pick("t", []) is None


def test_pick_classic_numbers_only_selectable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(widgets, "is_interactive", lambda: False)
    printed: list[str] = []
    answers = iter(["3", "1-2 4"])
    monkeypatch.setattr(classic.color, "color_print", printed.append)
    monkeypatch.setattr(classic.color, "color_input", lambda _: next(answers))
    assert tui.pick("t", SECTIONS) == tui.ListResult((4,))
    shown = printed[0].splitlines()
    assert shown == [
        "<@q>■ A</>",
        " 1. <@t>a1</>",
        " 2. <@t>a2</>",
        "<@q>■ B</>",
        " 3. <@t>b1</>",
    ]
    # multi: "4" is the trailing "all" entry
    assert tui.pick("t", SECTIONS, multi=True) == tui.ListResult((1, 2, 4))
