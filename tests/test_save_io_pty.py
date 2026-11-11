"""Getting the save in and out in a real 100x30 pseudo-terminal, locale ja.

- The editor itself (``python -m bcsfe`` in a sandbox, see conftest.py): the
  start menu order with and without a last save, the XP field with ``max``,
  the multi-item edit defaulting to "all at once".
- tests/tui_scripts/io_demo.py for the download and upload flows, which need
  a fake game server (the editor's process cannot be patched).

HOME, the XDG folders and every editor path point into the sandbox; the
proxies are unroutable.  The download is never chosen in the real editor.
"""

from __future__ import annotations

import datetime
import filecmp
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from bcsfe.core.io.root_handler import RootHandler
from tests.conftest import FIXTURE_SAVE, PYTHON, REPO, Sandbox

if TYPE_CHECKING:
    from tests.tui_harness import Terminal

StartTerminal = Callable[..., "Terminal"]
STARTUP = 60.0  # the first start copies the editor files into the sandbox
IO_DEMO = REPO / "tests" / "tui_scripts" / "io_demo.py"
LIST_HINT = "文字入力で絞り込み"
MENU_TITLE = "セーブデータの読み込み方法を選択してください:"
SAVED_AT = datetime.datetime(2026, 11, 10, 21, 5)
# The device entry: root storage on Android, adb elsewhere (also on macOS,
# where /system used to match /System on the case-insensitive file system).
ROOT_ENTRY = (
    "root 権限でストレージからセーブデータを取得"
    if RootHandler().is_android()
    else "adb でデバイスからセーブデータを取得"
)


def env_of(sandbox: Sandbox) -> dict[str, str]:
    home = sandbox.root / "home"
    home.mkdir(exist_ok=True)
    return {**sandbox.env, "HOME": str(home)}


def start_menu_command(sandbox: Sandbox) -> list[str]:
    """The editor without ``-i``: it starts with the start menu."""
    argv = sandbox.editor_command(sandbox.root / "SAVE_DATA")
    i = argv.index("-i")
    return argv[:i] + argv[i + 2 :]


def menu_rows(term: Terminal) -> list[str]:
    """The start menu rows (pointer and padding removed), top to bottom."""
    lines = term.screen_lines()
    top = next(i for i, line in enumerate(lines) if line == MENU_TITLE)
    rows: list[str] = []
    for line in lines[top + 2 :]:
        if LIST_HINT in line:
            break
        rows.append(line[2:].rstrip())
    return rows


def put_last_save(sandbox: Sandbox) -> Path:
    path = sandbox.data_dir / "saves" / "SAVE_DATA"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_SAVE, path)
    os.utime(path, (SAVED_AT.timestamp(), SAVED_AT.timestamp()))
    return path


def test_start_menu_puts_the_download_first(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja")
    term = terminal(start_menu_command(editor_sandbox), env=env_of(editor_sandbox))
    term.wait_for(LIST_HINT, STARTUP)
    term.wait_idle(0.3)
    assert "❯ 引継ぎコードでセーブデータを取得" in term.screen_text()
    assert menu_rows(term) == [
        "引継ぎコードでセーブデータを取得",
        "ファイルからセーブデータを選択",
        ROOT_ENTRY,
        "JSON からセーブデータを読み込み",
        "設定を編集",
        "外部コンテンツの更新を確認",
        "ゲームデータの管理",
        "このエディターについて",
        "終了",
    ]
    term.send_key("end")
    term.send_key("enter")
    assert term.wait_exit(30) == 0


def test_start_menu_offers_the_last_save(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(locale="ja")
    path = put_last_save(editor_sandbox)
    term = terminal(start_menu_command(editor_sandbox), env=env_of(editor_sandbox))
    term.wait_for(LIST_HINT, STARTUP)
    term.wait_idle(0.3)
    rows = menu_rows(term)
    assert rows[:3] == [
        "引継ぎコードでセーブデータを取得",
        "前回のセーブデータを開く (2026-11-10 21:05 保存、ゲームバージョン 16.0.0)",
        "ファイルからセーブデータを選択",
    ]
    assert len(rows) == 10
    term.send_key("down")
    term.send_key("enter")
    term.wait_for("セーブデータを読み込んでいます", 30)
    flat = "".join(term.screen_lines())  # the path may wrap
    assert str(path) in flat


# -- the editor on the fixture save -------------------------------------------------


def start_editor(terminal: StartTerminal, sandbox: Sandbox) -> Terminal:
    """The editor on the fixture save, at the feature menu."""
    sandbox.write_config(locale="ja")
    term = terminal(
        sandbox.editor_command(sandbox.root / "SAVE_DATA"), env=env_of(sandbox)
    )
    wait_any(term, ("入力:", LIST_HINT), STARTUP)
    term.wait_idle(0.3)
    return term


def wait_any(term: Terminal, texts: tuple[str, ...], timeout: float) -> str:
    """Wait until one of ``texts`` is on the screen; returns it."""
    deadline = time.monotonic() + timeout
    while True:
        screen = term.screen_text()
        for text in texts:
            if text in screen:
                return text
        if time.monotonic() > deadline:
            raise TimeoutError(f"none of {texts!r} appeared\n{screen}")
        term.sleep(0.05)


def open_feature(term: Terminal, query: str, label: str, wait: str) -> None:
    """Open the feature called ``label`` from the feature menu: the classic
    prompt (type the name, pick it by number if several match) or a list
    widget (type the name, move to the row, Enter)."""
    if LIST_HINT not in term.screen_text():  # the classic "入力:" prompt
        term.send_text(query + "\r")
        found = wait_any(term, (f". {label}", wait), 30)
        if found != wait:
            term.wait_idle(0.3)
            number = next(
                m.group(1)
                for line in reversed(term.screen_lines())
                if (m := re.match(r"\s*(\d+)\. (.*)$", line)) and m.group(2) == label
            )
            term.send_text(number + "\r")
    else:
        term.send_text(query)
        term.wait_idle(0.3)
        for _ in range(30):
            if any(
                line.startswith("❯ ") and line[2:].startswith(label)
                for line in term.screen_lines()
            ):
                break
            term.send_key("down")
            term.wait_idle(0.1)
        term.send_key("enter")
    term.wait_for(wait, 30)


def test_xp_field_takes_max(terminal: StartTerminal, editor_sandbox: Sandbox) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_feature(term, "xp", "XP", "max で最大値 (99,999,999)")
    screen = term.screen_text()
    assert "範囲 0〜99,999,999  現在値 20,870,087" in screen
    term.send_text("max")
    term.send_key("enter")
    term.wait_for("XP を 99999999 に変更しました")
    assert "✔ XP の値を入力してください" in term.screen_text()


def test_multi_item_edit_defaults_to_all_at_once(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_feature(term, "マタタビ", "マタタビ / 獣石", "Ctrl-A 表示中をすべて選択")
    term.send_key("tab", 2)
    term.wait_for("2 件選択中")
    term.send_key("enter")
    term.wait_for("まとめて編集しますか")
    term.wait_idle(0.3)
    screen = term.screen_text()
    assert "❯ まとめて編集" in screen
    assert "  個別に編集" in screen
    term.send_key("enter")
    term.wait_for("最大値: 998")
    term.send_text("max")
    term.send_key("enter")
    term.wait_for("を 998 に変更しました")


# -- download and upload with a fake server (tests/tui_scripts/io_demo.py) ------------


def demo(
    terminal: StartTerminal, sandbox: Sandbox, flow: str, *options: str
) -> Terminal:
    command = [PYTHON, str(IO_DEMO), flow, "--sandbox", str(sandbox.root), *options]
    return terminal(command, env=env_of(sandbox))


def test_start_menu_download_then_load(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    """The common path: Enter on the first entry, the codes, Enter on jp; the
    downloaded save (here the fixture) is written to saves/SAVE_DATA and loaded."""
    editor_sandbox.write_config(locale="ja")
    server_save = str(editor_sandbox.root / "SAVE_DATA")
    term = demo(terminal, editor_sandbox, "start", "--save", server_save)
    term.wait_for(LIST_HINT, 30)
    term.wait_idle(0.3)
    term.send_key("enter")
    term.wait_for("引継ぎコードを入力してください:")
    term.send_text("abc123def")
    term.send_key("enter")
    term.wait_for("認証番号を入力してください:")
    term.send_text("1234")
    term.send_key("enter")
    term.wait_for("国コードを選択してください:")
    term.wait_idle(0.3)
    term.send_key("enter")
    term.wait_for("RESULT:", 30)
    assert "RESULT: ('16.0.0', False)" in term.screen_text()
    saved = editor_sandbox.data_dir / "saves" / "SAVE_DATA"
    # filecmp: a failure must not print the (private) save bytes
    assert filecmp.cmp(saved, FIXTURE_SAVE, shallow=False)
    flat = "".join(term.screen_lines())
    assert f"セーブデータを読み込んでいます: {saved}" in flat


def test_download_screens(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = demo(terminal, sandbox, "download")
    term.wait_for("引継ぎコードを入力してください:")
    term.send_key("enter")
    term.wait_for("値を入力してください")  # empty: the field stays open
    term.send_text("ａｂｃ１２３ｄｅｆ")  # full width, as an IME types it
    term.send_key("enter")
    term.wait_for("認証番号を入力してください:")
    assert "✔ 引継ぎコードを入力してください: ａｂｃ１２３ｄｅｆ" in term.screen_text()
    term.send_text("１２３４")
    term.send_key("enter")
    term.wait_for("国コードを選択してください:")
    term.wait_idle(0.3)
    assert "❯ jp" in term.screen_text()
    term.send_key("enter")
    term.wait_for("RESULT:")
    flat = "".join(term.screen_lines())
    assert "引継ぎコード: abc123def、認証番号: 1234、国コード: jp" in flat
    saved = sandbox.root / "data" / "saves" / "SAVE_DATA"
    assert f"セーブデータを {saved} にダウンロードしました" in flat
    assert saved.read_bytes() == b"not a real save"
    assert (sandbox.root / "data" / "fork" / "state.json").is_file()


def test_upload_screen(terminal: StartTerminal, sandbox: Sandbox) -> None:
    term = demo(terminal, sandbox, "upload")
    term.wait_for("RESULT:")
    lines = term.screen_lines()
    top = lines.index("┌" + "─" * 27 + "┐")
    assert lines[top - 1].startswith("アップロードしました。")
    assert lines[top + 1 : top + 5] == [
        "│  引継ぎコード  abc123def  │",
        "│  認証番号      1234       │",
        "└" + "─" * 27 + "┘",
        "引継ぎコードをクリップボードにコピーしました。",
    ]
    assert "CLIPBOARD: abc123def" in term.screen_text()
