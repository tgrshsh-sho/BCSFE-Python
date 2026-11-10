"""The widgets in a real pseudo-terminal (tests/tui_scripts/widget_demo.py)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from tests.conftest import PYTHON, Sandbox

if TYPE_CHECKING:
    from tests.tui_harness import Terminal

StartTerminal = Callable[..., "Terminal"]


def start_demo(
    terminal: StartTerminal, sandbox: Sandbox, widget: str, **env: str
) -> Terminal:
    return terminal(sandbox.demo_command(widget), env={**sandbox.env, **env})


def test_select_hiragana_query_filters_katakana(
    terminal: StartTerminal, sandbox: Sandbox
) -> None:
    term = start_demo(terminal, sandbox, "select")
    term.wait_for("文字入力で絞り込み")
    assert "20 / 20" in term.screen_text()
    term.send_text("ねこ")
    term.wait_for("8 / 20")
    lines = term.screen_lines()
    assert lines[0] == "アイテムを選んでください:"
    assert lines[1].startswith("> ねこ")
    assert lines[2].startswith("❯ ネコ缶")
    assert lines[2].endswith("45,000")  # hint, right-aligned
    screen = term.screen_text()
    for name in ("ネコビタンA", "ﾈｺｶﾝ半額チケット", "ねこじゃらし", "おかしなネコ缶"):
        assert name in screen
    assert "XP" not in screen
    term.send_key("down")
    term.send_key("enter")
    term.wait_for("RESULT: 2")
    screen = term.screen_text()
    assert "✔ アイテムを選んでください: ネコビタンA" in screen
    assert "絞り込み" not in screen  # the widget was erased


def test_multi_select_checks_rows(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = start_demo(terminal, sandbox, "multi")
    term.wait_for("Ctrl-A 表示中をすべて選択")
    term.send_text("マタタビ")
    term.wait_for("4 / 20")
    term.send_key("tab", 2)
    term.wait_for("2 件選択中")
    lines = term.screen_lines()
    assert lines[2].startswith("  [x] 赤マタタビの種")
    assert lines[3].startswith("  [x] 青マタタビの種")
    assert lines[4].startswith("❯ [ ] 緑マタタビの種")
    term.send_key("c-u")  # checks survive clearing the query
    term.wait_for("2 件選択中  20 / 20")
    term.send_key("enter")
    term.wait_for("RESULT: [10, 11]")
    assert (
        "✔ 編集するアイテムを選んでください: 赤マタタビの種 / 青マタタビの種"
        in term.screen_text()
    )


def test_confirm_keys(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = start_demo(terminal, sandbox, "confirm")
    term.wait_for("[ いいえ ]")  # default answer highlighted
    term.send_key("left")
    term.wait_for("[ はい ]")
    term.send_text("n")  # one key answers at once
    term.wait_for("RESULT: False")
    assert "✔ 本当に続けますか? いいえ" in term.screen_text()


def test_confirm_escape_is_quick(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = start_demo(terminal, sandbox, "confirm")
    term.wait_for("[ いいえ ]")
    start = time.monotonic()
    term.send_key("esc")
    term.wait_for("RESULT: None")
    assert time.monotonic() - start < 1.0
    assert "✘ 本当に続けますか? (キャンセル)" in term.screen_text()


def test_integer_validation_error_keeps_input(
    terminal: StartTerminal, sandbox: Sandbox
) -> None:
    term = start_demo(terminal, sandbox, "integer")
    term.wait_for("範囲 0〜99,999,999  現在値 1,234")
    term.send_text("abc")
    term.send_key("enter")
    term.wait_for("整数で入力してください")
    assert term.screen_lines()[1].startswith("> abc")
    term.send_key("c-u")
    term.send_text("１２３４５６７８９")  # full-width, out of range
    term.send_key("enter")
    term.wait_for("0〜99,999,999 の範囲で入力してください")
    term.send_key("c-u")
    term.send_key("enter")  # empty Enter keeps the current value
    term.wait_for("RESULT: 1234")
    assert "✔ XP の値を入力してください: 1234" in term.screen_text()


def test_text_validation(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = start_demo(terminal, sandbox, "text")
    term.wait_for("Enter 決定")
    term.send_key("enter")
    term.wait_for("空欄にはできません")
    term.send_text("ネコ")
    term.wait_gone("空欄にはできません")  # typing clears the error
    term.send_key("enter")
    term.wait_for("RESULT: 'ネコ'")


def test_range_input_accepts_ime_style_text(
    terminal: StartTerminal, sandbox: Sandbox
) -> None:
    term = start_demo(terminal, sandbox, "range")
    term.wait_for("Enter")
    term.send_text("60")
    term.send_key("enter")
    term.wait_for("✘")  # 60 > 48: rejected inline, input kept
    assert term.screen_lines()[1].startswith("> 60")
    term.send_key("c-u")
    term.send_text("１〜３、５")
    term.send_key("enter")
    term.wait_for("RESULT: [1, 2, 3, 5]")


def test_large_list_stays_responsive(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = start_demo(terminal, sandbox, "big")
    term.wait_for("2000 / 2000")
    start = time.monotonic()
    term.send_text("1999")
    term.wait_for("1 / 2000")
    assert time.monotonic() - start < 2.0
    term.send_key("enter")
    term.wait_for("RESULT: 1999")
    term.send_key("enter")  # nothing else is waiting; the script has exited
    assert term.wait_exit() == 0


def test_classic_mode_env_uses_numbered_prompt(
    terminal: StartTerminal, sandbox: Sandbox
) -> None:
    term = start_demo(terminal, sandbox, "select", BCSFE_UI="classic")
    term.wait_for(" 20. ")
    assert " 1. ネコ缶" in term.screen_text()
    assert "絞り込み" not in term.screen_text()
    term.send_text("3\r")
    term.wait_for("RESULT: 2")


def test_widget_starts_on_a_fresh_line(
    terminal: StartTerminal, sandbox: Sandbox
) -> None:
    script = (
        "from bcsfe.cli import tui\n"
        "print('partial line', end='', flush=True)\n"
        "print('RESULT:', tui.select('Pick:', ['alpha', 'beta']))\n"
    )
    term = terminal([PYTHON, "-c", script], env=sandbox.env)
    term.wait_for("Pick:")
    lines = term.screen_lines()
    assert lines[0] == "partial line"
    assert lines[1] == "Pick:"
    term.send_text("bet")
    term.send_key("enter")
    term.wait_for("RESULT: 1")
    assert term.screen_lines()[1] == "✔ Pick: beta"


def test_tall_title_stays_in_scrollback(
    terminal: StartTerminal, sandbox: Sandbox
) -> None:
    script = (
        "from bcsfe.cli import tui\n"
        "lines = [f'warning line {i}' for i in range(1, 13)] + ['Continue?']\n"
        "print('RESULT:', tui.confirm('\\n'.join(lines)))\n"
    )
    term = terminal([PYTHON, "-c", script], env=sandbox.env, rows=10)
    term.wait_for("[ No ]")
    term.send_text("y")
    term.wait_for("RESULT: True")
    history = term.history_lines()
    assert [f"warning line {i}" for i in range(1, 13)] == history[:12]
    assert history[12] == "✔ Continue? Yes"
