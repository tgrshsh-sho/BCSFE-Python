"""The cat picker in the real editor (pseudo-terminal, locale ja), the level
input, and the classic flow with piped stdin.

Features are opened by typing their name and Enter, which works in the
classic feature menu (a plain ``input()``) and in the interactive home menu
(the first match is highlighted).  The editor runs in a sandbox (HOME and the
XDG folders included) with an unroutable proxy, see conftest.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from tests.conftest import PYTHON, REPO, Sandbox

if TYPE_CHECKING:
    from tests.tui_harness import Terminal

StartTerminal = Callable[..., "Terminal"]
STARTUP = 60.0  # first start copies the editor files into the sandbox
MENUS = ("入力:", "機能を選んでください:")  # classic feature menu / home menu
PICKER = "Ctrl-O その他の選び方"  # last line of the picker
UNLOCK = "キャラの入手"  # feature: unlock / remove cats
UPGRADE = "キャラのレベル"  # feature: upgrade cats
DEMO = REPO / "tests" / "tui_scripts" / "cat_picker_demo.py"
# "❯ [ ] 882 監獄ネコ      EX          未所持": id, name, rarity, level
ROW = re.compile(r"^(?:❯ |  )\[[ x]\] +(\d+) (.*?)\s{2,}(\S+)\s+(\S+)$")


def sandbox_home(sandbox: Sandbox) -> dict[str, str]:
    """The sandbox environment with HOME inside the sandbox too."""
    home = sandbox.root / "home"
    home.mkdir(exist_ok=True)
    return {**sandbox.env, "HOME": str(home)}


def wait_any(term: Terminal, texts: Sequence[str], timeout: float = 30.0) -> str:
    """Wait until one of ``texts`` is on the screen; returns it."""
    deadline = time.monotonic() + timeout
    while True:
        screen = term.screen_text()
        for text in texts:
            if text in screen:
                return text
        if time.monotonic() > deadline:
            raise TimeoutError(f"none of {texts!r} after {timeout}s\n{screen}")
        term.sleep(0.05)


def wait_menu(term: Terminal, timeout: float = 30.0) -> None:
    wait_any(term, MENUS, timeout)
    term.wait_idle(0.3)


def start_editor(terminal: StartTerminal, sandbox: Sandbox) -> Terminal:
    sandbox.write_config(locale="ja")
    command = sandbox.editor_command(sandbox.root / "SAVE_DATA")
    term = terminal(command, env=sandbox_home(sandbox))
    wait_menu(term, STARTUP)
    return term


def start_demo(
    terminal: StartTerminal, sandbox: Sandbox, mode: str, name: str
) -> Terminal:
    command = [
        PYTHON,
        str(DEMO),
        mode,
        "--save",
        str(sandbox.root / "SAVE_DATA"),
        "--game-data",
        str(sandbox.game_data),
        "--sandbox",
        str(sandbox.root / name),
    ]
    term = terminal(command, env=sandbox_home(sandbox))
    term.wait_for("READY", STARTUP)
    return term


def open_picker(term: Terminal, feature: str = UNLOCK) -> float:
    """Type ``feature`` into the menu; seconds until the picker is shown."""
    start = time.monotonic()
    term.send_text(feature + "\r")
    term.wait_for(PICKER, 30)
    return time.monotonic() - start


def counter(term: Terminal) -> tuple[int, int, int]:
    """(checked, shown, total) from the picker's counter."""
    match = re.search(r"(\d+) 件選択中\s+(\d+) / (\d+)", term.screen_text())
    assert match is not None, term.screen_text()
    checked, shown, total = match.groups()
    return int(checked), int(shown), int(total)


def query(term: Terminal, text: str) -> None:
    """Replace the query and wait until the list was filtered."""
    term.send_key("c-u")
    term.send_text(text)
    term.wait_for("> " + text)
    term.wait_idle(0.2)


def rows(term: Terminal) -> list[tuple[int, str, str, str]]:
    """Visible rows: (id, name, rarity, level or "未所持")."""
    found: list[tuple[int, str, str, str]] = []
    for line in term.screen_lines():
        match = ROW.match(line)
        if match:
            found.append((int(match[1]), match[2].strip(), match[3], match[4]))
    return found


def lines_after(term: Terminal, marker: str) -> list[str]:
    lines = term.screen_lines()
    index = max(i for i, line in enumerate(lines) if marker in line)
    return lines[index + 1 :]


def test_unlock_cats_by_id_range(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)
    seconds = open_picker(term)
    assert seconds < 3.0, f"the picker took {seconds:.2f} s to open"
    screen = term.screen_text()
    assert "キャラを選んでください:" in screen
    assert "例: 未所持 超激レア / 16.0.0 / 882-886 / 名前の一部" in screen
    assert "キャラの選択方法を選んでください" not in screen  # no method menu
    assert counter(term) == (0, 888, 888)
    assert "❯ [ ]   0 ネコ" in screen
    assert rows(term)[0] == (0, "ネコ", "基本キャラ", "Lv20+0")

    query(term, "882-886 所持")
    term.wait_for("該当なし")  # none of them is owned yet
    query(term, "882-886")
    term.wait_for("5 / 888")
    assert rows(term) == [
        (882, "監獄ネコ", "EX", "未所持"),
        (883, "監獄ネコガール", "EX", "未所持"),
        (884, "監獄タンクネコ", "EX", "未所持"),
        (885, "監獄ちびネコ", "EX", "未所持"),
        (886, "悠久の冒険者エターニス", "超激レア", "未所持"),
    ]
    term.send_key("c-a")
    term.wait_for("5 件選択中")
    term.send_key("enter")

    # one summary line, then the action question with the safe choice first
    term.wait_for("キャラを入手しますか、削除しますか？:")
    screen = term.screen_text()
    assert (
        "✔ キャラを選んでください: 5 件: 882 監獄ネコ / 883 監獄ネコガール"
        " / 884 監獄タンクネコ ほか" in screen
    )
    assert "を選択中" not in screen and "選択を終了しますか" not in screen
    assert "❯ キャラを入手" in screen
    term.send_key("enter")
    term.wait_for("キャラを入手しました")
    wait_menu(term)

    # the picker shows them as owned now; Esc cancels the whole feature
    open_picker(term)
    query(term, "882-886 所持")
    term.wait_for("5 / 888")
    assert [(r[0], r[3]) for r in rows(term)] == [
        (882, "Lv1+0"),
        (883, "Lv1+0"),
        (884, "Lv1+0"),
        (885, "Lv1+0"),
        (886, "Lv1+0"),
    ]
    term.send_key("esc")
    term.wait_for("✘ キャラを選んでください: (キャンセル)")
    wait_menu(term)
    rest = lines_after(term, "✘ キャラを選んでください: (キャンセル)")
    assert not any("しますか" in line for line in rest), rest


def test_facet_and_name_queries(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_picker(term)

    query(term, "超激レア")
    _, uber, _ = counter(term)
    query(term, "所持 超激レア")
    _, owned, _ = counter(term)
    query(term, "未所持 超激レア")
    _, unowned, total = counter(term)
    assert 0 < unowned < uber < total and owned + unowned == uber
    shown = rows(term)
    assert len(shown) == min(15, unowned)
    assert all(r[2:] == ("超激レア", "未所持") for r in shown), shown

    query(term, "かんごく")  # hiragana finds the katakana reading of 監獄
    term.wait_for("4 / 888")
    assert [r[:2] for r in rows(term)] == [
        (882, "監獄ネコ"),
        (883, "監獄ネコガール"),
        (884, "監獄タンクネコ"),
        (885, "監獄ちびネコ"),
    ]
    query(term, "16.0.0 ガチャ以外")
    term.wait_for("4 / 888")
    query(term, "８８６〜８８２")  # full-width, reversed: in the order typed
    term.wait_for("5 / 888")
    assert [r[0] for r in rows(term)] == [886, 885, 884, 883, 882]
    query(term, "887")
    term.wait_for("1 / 888")
    assert rows(term) == [(887, "(名前なし)", "超激レア", "未所持")]


def test_ctrl_o_runs_the_method_menu(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_picker(term)
    query(term, "伝説レア")
    _, legend, _ = counter(term)
    query(term, "882 883")
    term.wait_for("2 / 888")
    term.send_key("c-a")
    term.wait_for("2 件選択中")

    term.send_key("c-o")
    term.wait_for("キャラの選択方法を選んでください:")
    term.wait_for("ゲームバージョンでキャラを選択")
    screen = term.screen_text()
    assert "ガチャでキャラを選択" in screen and "完了" not in screen
    assert "11 / 11" in screen
    term.send_key("esc")  # back to the picker, checks kept
    term.wait_for(PICKER)
    assert counter(term)[0] == 2

    term.send_key("c-o")
    term.wait_for("ゲームバージョンでキャラを選択")
    term.send_text("レア度")
    term.wait_for("1 / 11")
    term.send_key("enter")
    term.wait_for("キャラのレア度を選択してください:")
    term.send_text("伝説")
    term.wait_for("1 / 6")
    term.send_key("tab")
    term.send_key("enter")
    term.wait_for("キャラを入手しますか、削除しますか？:")
    screen = term.screen_text()
    assert f"選択中のキャラ ({legend + 2} 体): " in screen
    assert "✔ キャラのレア度を選択してください: 伝説レア" in screen
    term.send_key("esc")
    term.wait_for("✘ キャラを入手しますか、削除しますか？: (キャンセル)")


def test_upgrade_level_input(terminal: StartTerminal, editor_sandbox: Sandbox) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_picker(term, UPGRADE)
    query(term, "0-2")
    term.wait_for("3 / 888")
    term.send_key("c-a")
    term.send_key("enter")
    term.wait_for("レベルの変更方法を選択してください:")
    term.send_text("すべて")
    term.wait_for("1 / 2")
    term.send_key("enter")

    term.wait_for("体のレベルを入力してください")
    screen = term.screen_text()
    assert "例: 50+30 / max+max / 5-10+20-30 (範囲でランダム)" in screen
    # a bare level resets the plus level (upstream's rule): the hint says so
    assert "50+ は +レベルをそのまま、50 だけだと +レベルは 0 になります" in screen
    assert re.search(r"3 体のレベルを入力してください \(最大 \d+\+\d+\):", screen)
    assert "レベルを入力してください。例:" not in screen  # no 10-line help
    term.send_text("abc")
    term.send_key("enter")
    term.wait_for("✘ レベルが正しくありません: abc")
    term.send_key("c-u")
    term.send_text("５０＋３０")  # typed with the IME in full-width
    term.send_key("enter")
    term.wait_for("キャラのレベルを変更しました")
    wait_menu(term)

    # the base level is capped by upstream's user rank rules, the plus is not
    open_picker(term, UPGRADE)
    query(term, "0-2")
    term.wait_for("3 / 888")
    levels = [r[3] for r in rows(term)]
    assert len(levels) == 3 and all(re.fullmatch(r"Lv\d+\+30", lv) for lv in levels)


def test_classic_flow_with_piped_stdin(editor_sandbox: Sandbox) -> None:
    editor_sandbox.write_config(locale="ja")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BCSFE_")}
    env.update(sandbox_home(editor_sandbox))
    keys = "\n".join([UNLOCK, "4", "882-886", "y", "1", ""])
    proc = subprocess.run(
        editor_sandbox.editor_command(editor_sandbox.root / "SAVE_DATA"),
        input=keys,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=REPO,
        timeout=120,
    )
    out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", proc.stdout)
    assert " 4. ID でキャラを選択" in out and " 12. 完了" in out
    for line in ("監獄ネコ (882) を選択中", "悠久の冒険者エターニス (886) を選択中"):
        assert line in out
    assert "キャラの選択を終了しますか？ (y/n):" in out
    assert " 1. キャラを入手" in out
    assert "キャラを入手しました" in out
    assert "✔" not in out and "その他の選び方" not in out


def test_ui_mode_classic_keeps_the_method_menu(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(ui_mode="classic")
    term = start_editor(terminal, editor_sandbox)
    term.send_text(UNLOCK + "\r")
    term.wait_for("キャラの選択方法を選んでください:")
    screen = term.screen_text()
    assert " 4. ID でキャラを選択" in screen and " 12. 完了" in screen
    assert PICKER not in screen and "❯" not in screen


def test_picker_rows_are_built_quickly(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    """From before reading the 888 names to the picker on the screen."""
    term = start_demo(terminal, editor_sandbox, "pick", "demo")
    start = time.monotonic()
    term.wait_for(PICKER)
    seconds = time.monotonic() - start
    assert seconds < 1.5, f"the picker took {seconds:.2f} s to open"
    query(term, "882 883")
    term.send_key("c-a")
    term.send_key("c-o")  # the demo's method adds cats 0 and 1
    term.wait_for("RESULT: [0, 1, 882, 883]")
    assert "選択中のキャラ (4 体): 0 ネコ / 1 タンクネコ / 882 監獄ネコ" in (
        term.screen_text()
    )


def test_level_input_cancel_and_validation(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    title = "3 体のレベルを入力してください (最大 50+70):"
    term = start_demo(terminal, editor_sandbox, "upgrade", "demo1")
    term.wait_for(title)
    term.send_key("esc")
    term.wait_for("RESULT: (None, None, True)")

    term = start_demo(terminal, editor_sandbox, "upgrade", "demo2")
    term.wait_for(title)
    term.send_text("q\r")  # the classic quit key also cancels
    term.wait_for("RESULT: (None, None, True)")

    term = start_demo(terminal, editor_sandbox, "upgrade", "demo3")
    term.wait_for(title)
    term.send_key("enter")
    term.wait_for("✘ 値を入力してください")  # empty is rejected, not "+0"
    term.send_text("最大+\r")
    term.wait_for("RESULT: (49, -1, False)")
