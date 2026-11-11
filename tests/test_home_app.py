"""The home menu in the real editor, through a pseudo-terminal (100x30).

The editor runs on a copy of the fixture save in a sandbox (see conftest.py):
private data / config / XDG folders and an unroutable proxy.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import pytest

import bcsfe
from bcsfe import core
from tests.conftest import Sandbox
from tests.test_home import StartCore, core_starter

if TYPE_CHECKING:
    from tests.tui_harness import Terminal


@pytest.fixture
def start_core(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StartCore]:
    """In-process core, to read the save the editor wrote."""
    with core_starter(tmp_path_factory, monkeypatch) as start:
        yield start


StartTerminal = Callable[..., "Terminal"]
STARTUP = 60.0  # the first start copies the editor files into the sandbox
WAIT = 30.0

HOME_JA = "【よく使う】"
PROMPT_JA = "機能を選んでください:"
SAVE_Q_JA = "終了する前に最新の変更を保存しますか"


def start(terminal: StartTerminal, sandbox: Sandbox, ready: str = HOME_JA) -> Terminal:
    term = terminal(sandbox.editor_command(sandbox.root / "SAVE_DATA"), env=sandbox.env)
    term.wait_for(ready, STARTUP)
    term.wait_idle(0.3)
    return term


def wait_home(term: Terminal, heading: str = HOME_JA) -> None:
    term.wait_for(heading, WAIT)
    term.wait_idle(0.3)


def history(term: Terminal) -> str:
    return "\n".join(term.history_lines())


def edit_xp(term: Terminal, value: str = "12345") -> None:
    term.send_text("xp")
    term.wait_for("❯ XP", WAIT)
    term.send_key("enter")
    term.wait_for("XP の値を入力してください", WAIT)
    term.send_text(value)
    term.send_key("enter")
    term.wait_for(f"XP を {value} に変更しました", WAIT)
    wait_home(term)


def row_after(lines: list[str], text: str) -> str:
    index = next(i for i, line in enumerate(lines) if text in line)
    return lines[index + 1]


def test_start_header_and_home_summary(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja")
    term = start(terminal, editor_sandbox)

    log = history(term)
    version = bcsfe.get_full_version()
    assert f"にゃんこ大戦争 セーブデータエディター {version}" in log
    assert "BCSFE (fieryhenry 作) の日本語版です。" in log
    assert "編集すると BAN やセーブデータの破損が起こることがあります。" in log
    assert "このツールは無料です。お金を払って入手した場合は詐欺です。" in log
    # the long upstream text and the separate save line are gone
    assert "ようこそ" not in log and "謝辞" not in log
    assert "現在のバージョン" not in log and "現在のセーブデータ:" not in log

    screen = term.screen_lines()
    summary = next(i for i, line in enumerate(screen) if line.startswith("jp 16.0.0"))
    assert screen[summary - 1] == ""  # a blank line before the home
    assert "お問い合わせコード " in screen[summary] and "***" in screen[summary]
    assert "キャラ 123 / 888" in screen[summary]
    assert screen[summary + 1].startswith("ネコカン 8,673  XP 20,870,087  NP 5,329")
    assert (
        "レアチケット 373  プラチナチケット 12  レジェンドチケット 3"
        in screen[summary + 1]
    )
    assert screen[summary + 2] == PROMPT_JA
    # seeds while nothing was used yet, then the categories
    assert row_after(screen, HOME_JA).startswith(
        "❯ セーブデータをサーバーにアップロードして引継ぎコードと認証番号を取得"
    )
    assert row_after(screen, "❯ セーブデータをサーバーに").startswith(
        "  キャラの入手 / 削除"
    )
    assert any(line.strip() == "【セーブデータの管理】" for line in screen)
    assert any("Esc 終了" in line for line in screen)  # Esc exits from the home


def test_query_filters_and_enter_opens_the_xp_edit(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja")
    term = start(terminal, editor_sandbox)

    for query in ("xp", "ＸＰ"):
        term.send_text(query)
        term.wait_for("❯ XP", WAIT)
        term.wait_idle(0.3)
        screen = term.screen_lines()
        assert f"> {query}" in screen[screen.index(PROMPT_JA) + 1]
        assert not any("【" in line for line in screen)  # no headings
        xp_rows = [line for line in screen if re.fullmatch(r"[❯ ] XP +アイテム", line)]
        assert xp_rows == [xp_rows[0]] and xp_rows[0].startswith("❯")  # once, first
        assert not any("マタタビ" in line for line in screen)
        term.send_key("c-u")
        wait_home(term)

    edit_xp(term)

    screen = term.screen_lines()
    summary = next(i for i, line in enumerate(screen) if line.startswith("jp 16.0.0"))
    assert screen[summary + 1].startswith("ネコカン 8,673  XP 12,345  NP 5,329")
    assert screen[summary - 1] == ""
    log = history(term)
    assert f"✔ {PROMPT_JA} XP" in log  # what the list leaves in the scrollback
    # the used feature is the first favourite at once
    assert row_after(screen, HOME_JA).startswith("❯ XP")


def test_last_used_shows_first_on_the_next_start(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja")
    term = start(terminal, editor_sandbox)
    edit_xp(term)
    term.send_key("esc")
    term.wait_for(SAVE_Q_JA, WAIT)
    term.send_text("n")
    assert term.wait_exit(WAIT) == 0

    term = start(terminal, editor_sandbox)

    screen = term.screen_lines()
    assert row_after(screen, HOME_JA).startswith("❯ XP")
    assert row_after(screen, "❯ XP").startswith(
        "  セーブデータをサーバーにアップロードして"
    )
    assert (editor_sandbox.data_dir / "fork" / "usage.json").is_file()


def test_exit_question_esc_returns_home_and_yes_saves(
    terminal: StartTerminal, editor_sandbox: Sandbox, start_core: StartCore
) -> None:
    editor_sandbox.write_config(locale="ja")
    term = start(terminal, editor_sandbox)
    edit_xp(term)

    term.send_key("esc")
    term.wait_for(SAVE_Q_JA, WAIT)
    term.wait_idle(0.3)
    screen = term.screen_text()
    assert f"✘ {PROMPT_JA} (キャンセル)" in screen
    # Enter keeps the edits: "yes" is highlighted when there are changes
    assert "変更があります" in screen and "[ はい ]" in screen
    term.send_key("esc")
    term.wait_for(f"✘ {SAVE_Q_JA}？ (y/n): (キャンセル)", WAIT)
    wait_home(term)
    assert "XP 12,345" in term.screen_text()  # still the edited save

    term.send_key("c-c")  # Ctrl-C at the home also starts the exit flow
    term.wait_for(SAVE_Q_JA, WAIT)
    term.send_text("y")
    assert term.wait_exit(WAIT) == 0
    log = history(term)
    assert f"✔ {SAVE_Q_JA}？ (y/n): はい" in log
    assert "に保存しました" in log

    start_core("ja")  # read the written file in-process
    saved = core.SaveFile(core.Path(str(editor_sandbox.root / "SAVE_DATA")).read())
    assert saved.xp == 12345


def test_about_shows_the_full_text_and_the_config_in_use(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja")
    term = start(terminal, editor_sandbox)

    term.send_text("about")
    term.wait_for("❯ このエディターについて", WAIT)
    term.send_key("enter")
    wait_home(term)  # the filtered list had no headings: this is the next home

    log = history(term)
    assert f"現在のバージョン: {bcsfe.get_full_version()}" in log
    assert "へようこそ" in log and "謝辞:" in log
    assert "編集する前にセーブデータのバックアップを取ることをおすすめします。" in log
    # the config file in use (--config-path); the long line wraps at 100 columns
    rows = "".join(term.history_lines())
    assert f"設定ファイルの場所: {editor_sandbox.config_path}" in rows


def test_escape_in_a_feature_returns_to_the_home(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start(terminal, editor_sandbox, "[Frequently used]")  # locale: en

    term.send_text("catfruit")
    term.wait_for("❯ Catfruit / Behemoth Stones", WAIT)
    term.send_key("enter")
    term.wait_for("Ctrl-A check all shown", WAIT)
    term.send_key("esc")
    term.wait_for("✘ Select options for Catfruit / Behemoth Stones: (cancelled)", WAIT)
    wait_home(term, "[Frequently used]")

    assert "✔ Select a feature: Catfruit / Behemoth Stones" in history(term)


def test_switching_to_classic_hands_over_to_the_numbered_menu(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start(terminal, editor_sandbox, "[Frequently used]")  # locale: en

    term.send_text("config")
    term.wait_for("❯ Config", WAIT)
    term.send_key("enter")
    term.wait_for("Select a config option to edit:", WAIT)
    term.send_text("prompt style")
    term.wait_for("❯ Prompt style", WAIT)
    term.send_key("enter")
    term.wait_for("Select the prompt style:", WAIT)
    term.send_text("classic")
    term.wait_for("❯ Classic", WAIT)
    term.send_key("enter")
    term.wait_for("Successfully updated config", WAIT)

    term.wait_for("Input:", WAIT)
    log = history(term)
    assert "Current Save: jp-16.0.0" in log
    assert "Features:" in log


def test_classic_mode_keeps_the_numbered_menu(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja", ui_mode="classic")
    term = start(terminal, editor_sandbox, "入力:")

    log = history(term)
    assert f"にゃんこ大戦争 セーブデータエディター {bcsfe.get_full_version()}" in log
    assert "現在のセーブデータ: jp-16.0.0" in log
    assert "機能一覧:" in log and " 11. エディター" in log
    assert HOME_JA not in log
