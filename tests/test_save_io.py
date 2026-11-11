"""Getting the save in and out, in-process: start menu entries, download with
transfer codes, upload result and clipboard, number fields, file dialog.

The game servers are never contacted: ``core.ServerHandler`` is replaced by a
fake, ``core.RequestHandler`` fails loudly and the proxies point at an
unroutable address.  The clipboard tools are never run.  Every editor path,
HOME and the XDG folders point into a temporary sandbox.
"""

from __future__ import annotations

import builtins
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

import bcsfe
from bcsfe import core
from bcsfe.cli import (
    clipboard,
    color,
    dialog_creator,
    file_dialog,
    save_management,
    server_cli,
    tui,
)
from bcsfe.cli.tui import widgets
from bcsfe.core import io

T = TypeVar("T")

ENTER = "\r"
ESC = "\x1b"
UP = "\x1b[A"
DOWN = "\x1b[B"
TAB = "\t"
BACKSPACE = "\x7f"
CTRL_C = "\x03"
CTRL_U = "\x15"

NO_NETWORK = "http://127.0.0.1:9"
SAVED_GLOBALS = (
    "config_path",
    "print_config_err",
    "log_path",
    "transfer_backup_path",
    "game_data_path",
    "data_dir_path",
)
REAL_COPY = clipboard.copy
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# -- sandbox ------------------------------------------------------------------------


def _no_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("the network must not be used in these tests")


@pytest.fixture
def io_env(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Editor data, config, log, HOME and XDG folders in a sandbox, locale ja,
    no network, no clipboard tool, the real fork locale files."""
    root = tmp_path_factory.mktemp("sandbox")
    (root / "home").mkdir()
    monkeypatch.setenv("HOME", str(root / "home"))
    for name in ("DATA", "CONFIG", "STATE", "CACHE"):
        folder = root / "xdg" / name.lower()
        folder.mkdir(parents=True)
        monkeypatch.setenv(f"XDG_{name}_HOME", str(folder))
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, NO_NETWORK)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.delenv("BCSFE_UI", raising=False)
    shutil.copytree(Path(bcsfe.__file__).parent / "files", root / "data")
    (root / "config.yaml").write_text("locale: ja\n", encoding="utf-8")

    saved_globals = {name: getattr(core, name) for name in SAVED_GLOBALS}
    saved_state = dict(core.core_data.__dict__)
    core.set_data_dir_path(core.Path(str(root / "data")))
    core.set_config_path(core.Path(str(root / "config.yaml")))
    core.set_log_path(core.Path(str(root / "bcsfe.log")))
    core.set_game_data_path(core.Path(str(root / "game_data")))
    core.core_data.init_data()
    monkeypatch.setattr(core.RequestHandler, "get", _no_network)
    monkeypatch.setattr(core.RequestHandler, "post", _no_network)
    monkeypatch.setattr(clipboard.subprocess, "run", _no_clipboard_tool)
    monkeypatch.setattr(io.root_handler.RootHandler, "is_android", lambda self: False)
    try:
        yield root
    finally:
        for name, value in saved_globals.items():
            setattr(core, name, value)
        core.core_data.__dict__.clear()
        core.core_data.__dict__.update(saved_state)


def _no_clipboard_tool(*args: object, **kwargs: object) -> None:
    raise AssertionError("a clipboard tool must not run in these tests")


def set_interactive(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(tui, "is_interactive", lambda: value)
    monkeypatch.setattr(widgets, "is_interactive", lambda: value)


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    set_interactive(monkeypatch, True)


@pytest.fixture
def classic(monkeypatch: pytest.MonkeyPatch) -> None:
    set_interactive(monkeypatch, False)


def drive(func: Callable[[], T], *keys: str, timeout: float = 10.0) -> T:
    """Run ``func`` with the keys already typed (one prompt after another).

    A prompt still waiting after ``timeout`` seconds is cancelled with Ctrl-C
    and the test fails, instead of hanging."""
    with create_pipe_input() as pipe:
        for key in keys:
            pipe.send_text(key)
        done = threading.Event()
        stuck = threading.Event()

        def watchdog() -> None:
            if done.wait(timeout):
                return
            stuck.set()
            while not done.wait(0.5):
                pipe.send_text(CTRL_C)

        thread = threading.Thread(target=watchdog, daemon=True)
        thread.start()
        try:
            with create_app_session(input=pipe, output=DummyOutput()):
                result = func()
        finally:
            done.set()
            thread.join()
        if stuck.is_set():
            raise AssertionError("a prompt waited for keys that were not given")
        return result


def plain(text: str) -> str:
    """Printed text without the terminal colour codes."""
    return ANSI.sub("", text)


def text_of(key: str, **kwargs: Any) -> str:
    """A locale key as the user sees it (markup applied, colours removed)."""
    return plain(color.colorize(core.localize(key, **kwargs)))


# -- fake server ---------------------------------------------------------------------


class FakeSave:
    """What ``download_save`` uses of the downloaded save."""

    def to_file(self, path: core.Path) -> None:
        Path(path.path).parent.mkdir(parents=True, exist_ok=True)
        Path(path.path).write_bytes(b"downloaded")


class FakeServer:
    """Stands in for core.ServerHandler; records what the editor asked for."""

    downloads: list[tuple[str, str, str]] = []
    download_ok = True
    codes: tuple[str, str] | None = ("abc123def", "1234")

    def __init__(self, save_file: Any, print: bool = True) -> None:
        self.save_file = save_file

    @staticmethod
    def from_codes(
        transfer_code: str,
        confirmation_code: str,
        cc: core.CountryCode,
        gv: core.GameVersion,
        print: bool = True,
        save_backup: bool = True,
    ) -> tuple[FakeServer | None, object | None]:
        FakeServer.downloads.append((transfer_code, confirmation_code, cc.get_code()))
        if not FakeServer.download_ok:
            return None, object()  # the server answered, but not with a save
        return FakeServer(FakeSave()), object()

    def get_codes(self, upload_managed_items: bool = True, tries: int = 3) -> Any:
        return FakeServer.codes


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> type[FakeServer]:
    monkeypatch.setattr(FakeServer, "downloads", [])
    monkeypatch.setattr(FakeServer, "download_ok", True)
    monkeypatch.setattr(FakeServer, "codes", ("abc123def", "1234"))
    monkeypatch.setattr(core, "ServerHandler", FakeServer)

    def no_dialog(*args: object, **kwargs: object) -> None:
        raise AssertionError("no file dialog after a download")

    monkeypatch.setattr(file_dialog.FileDialog, "__init__", no_dialog)
    return FakeServer


def state_file() -> Path:
    return Path(core.Path.get_data_folder().path) / "fork" / "state.json"


def download(*keys: str) -> tuple[core.Path, core.CountryCode] | None:
    return drive(lambda: server_cli.ServerCLI().download_save(), *keys)


# -- codes ---------------------------------------------------------------------------


def test_codes_are_normalized() -> None:
    # full-width letters / digits and spaces from a Japanese IME, tabs, newlines
    assert server_cli.normalize_code("ａｂｃ１２３　ｄｅｆ") == "abc123def"
    assert server_cli.normalize_code(" 12 34\t\n") == "1234"
    assert server_cli.normalize_code("abc123def") == "abc123def"


def test_code_validation_rules_out_only_what_cannot_work(io_env: Path) -> None:
    empty = server_cli.transfer_code_error("")
    assert empty == "値を入力してください"
    assert server_cli.confirmation_code_error("") == empty
    assert server_cli.transfer_code_error("abc123def") is None
    assert server_cli.transfer_code_error("ABC-123_x.y~z") is None  # kept as typed
    problem = server_cli.transfer_code_error("ab/c?d#")
    assert problem is not None
    assert plain(color.colorize(problem)).endswith("使えません: / ? #")
    assert server_cli.confirmation_code_error("12/34") is None  # sent as JSON text


# -- download ------------------------------------------------------------------------


def test_download_with_ime_codes_saves_to_the_standard_path(
    io_env: Path, interactive: None, server: type[FakeServer]
) -> None:
    result = download("ａｂｃ １２３　ｄｅｆ" + ENTER, " １２３４ " + ENTER, ENTER)
    assert result is not None
    path, cc = result
    assert cc == "jp"  # highlighted by default
    assert server.downloads == [("abc123def", "1234", "jp")]
    assert path.path == core.SaveFile.get_saves_path().add("SAVE_DATA").path
    assert Path(path.path).read_bytes() == b"downloaded"
    assert json.loads(state_file().read_text(encoding="utf-8")) == {
        "download_region": "jp"
    }


def test_download_prints_where_the_save_went(
    io_env: Path,
    interactive: None,
    server: type[FakeServer],
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = download("abc123def" + ENTER, "1234" + ENTER, ENTER)
    assert result is not None
    out = plain(capsys.readouterr().out)
    assert "引継ぎコード: abc123def、認証番号: 1234、国コード: jp" in out
    assert text_of("save_downloaded", path=result[0].path) in out


def test_download_rejects_empty_and_url_breaking_codes(
    io_env: Path, interactive: None, server: type[FakeServer]
) -> None:
    keys = [
        ENTER,  # empty: rejected, the field stays open
        "ab/c" + ENTER,  # "/": rejected, input kept
        CTRL_U + "abc" + ENTER,
        "　" + ENTER,  # only an ideographic space: empty
        BACKSPACE + "1234" + ENTER,
        ENTER,
    ]
    assert download(*keys) is not None
    assert server.downloads == [("abc", "1234", "jp")]


def test_download_region_defaults_to_the_last_successful_one(
    io_env: Path, interactive: None, server: type[FakeServer]
) -> None:
    assert download("abc" + ENTER, "1" + ENTER, DOWN + DOWN + ENTER) is not None
    assert server.downloads[-1][2] == "tw"  # en jp kr tw: two down from jp
    result = download("abc" + ENTER, "1" + ENTER, ENTER)
    assert result is not None and result[1] == "tw"


def test_download_region_state_keeps_other_keys_and_ignores_bad_values(
    io_env: Path, interactive: None, server: type[FakeServer]
) -> None:
    state_file().parent.mkdir(parents=True)
    state_file().write_text('{"download_region": "kr", "other": [1]}', "utf-8")
    result = download("abc" + ENTER, "1" + ENTER, ENTER)
    assert result is not None and result[1] == "kr"
    saved = json.loads(state_file().read_text(encoding="utf-8"))
    assert saved == {"download_region": "kr", "other": [1]}
    assert sorted(p.name for p in state_file().parent.iterdir()) == ["state.json"]

    state_file().write_text('{"download_region": "xx"}', "utf-8")
    assert server_cli.last_download_region() == "jp"


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "\udcff", ""])
def test_corrupt_state_file_is_ignored(
    io_env: Path, interactive: None, server: type[FakeServer], content: str
) -> None:
    state_file().parent.mkdir(parents=True)
    state_file().write_bytes(content.encode("utf-8", "surrogateescape"))
    result = download("abc" + ENTER, "1" + ENTER, ENTER)
    assert result is not None and result[1] == "jp"
    assert json.loads(state_file().read_text(encoding="utf-8")) == {
        "download_region": "jp"
    }


def test_download_survives_an_unwritable_state(
    io_env: Path,
    interactive: None,
    server: type[FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(server_cli.os, "replace", broken)
    result = download("abc" + ENTER, "1" + ENTER, ENTER)
    assert result is not None
    assert not state_file().exists()
    assert sorted(p.name for p in state_file().parent.iterdir()) == []  # no temp


def test_cancel_or_failed_download_changes_nothing(
    io_env: Path,
    interactive: None,
    server: type[FakeServer],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Esc / Ctrl-C at each step (a lone Esc after other keys of the same pipe
    # write would wait for the rest of an escape sequence)
    assert download(ESC) is None
    assert download("abc" + ENTER, CTRL_C) is None
    assert download("abc" + ENTER, "1" + ENTER, CTRL_C) is None
    assert server.downloads == []

    server.download_ok = False
    assert download("abc" + ENTER, "1" + ENTER, ENTER) is None
    out = plain(capsys.readouterr().out)
    assert text_of("invalid_codes_error") in out
    assert text_of("jp_tw_mixup") in out
    assert not state_file().exists()
    assert not core.SaveFile.get_save_path().exists()


def test_classic_download_normalizes_and_numbers_the_regions(
    io_env: Path,
    classic: None,
    server: type[FakeServer],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["", "ａｂｃ", "１２３４", "4"])
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(plain(prompt))
        return next(answers)

    monkeypatch.setattr(color, "color_input", answer)
    result = download()
    assert result is not None and result[1] == "tw"
    assert server.downloads == [("abc", "1234", "tw")]
    assert prompts[:2] == [text_of("enter_transfer_code")] * 2  # empty: asked again
    out = plain(capsys.readouterr().out)
    assert "値を入力してください" in out
    assert " 1. en\n 2. jp\n 3. kr\n 4. tw" in out


# -- start menu ----------------------------------------------------------------------


@pytest.fixture
def menu(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Labels of each start menu shown (the menu itself returns "cancelled")."""
    shown: list[list[str]] = []

    def capture(
        actions: dialog_creator.Actions[Any], dialog_key: str, *a: Any, **k: Any
    ) -> None:
        assert dialog_key == "save_load_option"
        shown.append(
            [plain(color.colorize(o)) for act in actions.actions for o in act.options]
        )
        return None

    monkeypatch.setattr(dialog_creator, "single_select_key", capture)
    return shown


def write_save_head(game_version: int, when: datetime.datetime) -> Path:
    path = Path(core.SaveFile.get_save_path().path)
    path.write_bytes(game_version.to_bytes(4, "little") + b"\0" * 60)
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


COMMON = ["ファイルからセーブデータを選択", "adb でデバイスからセーブデータを取得"]
STARTING = [
    "JSON からセーブデータを読み込み",
    "設定を編集",
    "外部コンテンツの更新を確認",
    "ゲームデータの管理",
    "このエディターについて",
    "終了",
]
# the classic menu keeps upstream's entries (no "about this editor")
STARTING_CLASSIC = [entry for entry in STARTING if entry != "このエディターについて"]


def test_start_menu_puts_the_download_first(
    io_env: Path, interactive: None, menu: list[list[str]]
) -> None:
    assert save_management.SaveManagement.select_save(True) == (None, False)
    assert menu[-1] == ["引継ぎコードでセーブデータを取得", *COMMON, *STARTING]


def test_start_menu_offers_the_last_save_with_time_and_version(
    io_env: Path, interactive: None, menu: list[list[str]]
) -> None:
    write_save_head(160000, datetime.datetime(2026, 11, 10, 21, 5))
    save_management.SaveManagement.select_save(True)
    assert menu[-1] == [
        "引継ぎコードでセーブデータを取得",
        "前回のセーブデータを開く (2026-11-10 21:05 保存、ゲームバージョン 16.0.0)",
        *COMMON,
        *STARTING,
    ]
    save_management.SaveManagement.select_save(False)  # the "load save" feature
    assert menu[-1][:3] == menu[-2][:3] and menu[-1][-1] == STARTING[0]


def test_start_menu_falls_back_to_the_path_for_an_unreadable_save(
    io_env: Path, interactive: None, menu: list[list[str]]
) -> None:
    path = write_save_head(160000, datetime.datetime(2026, 11, 10, 21, 5))
    path.write_bytes(b"\1\2")  # too short for a game version
    save_management.SaveManagement.select_save(True)
    assert menu[-1][1] == f"{path} からセーブデータを読み込み"


def test_classic_start_menu_keeps_upstream_order(
    io_env: Path, classic: None, menu: list[list[str]]
) -> None:
    path = write_save_head(160000, datetime.datetime(2026, 11, 10, 21, 5))
    save_management.SaveManagement.select_save(True)
    assert menu[-1] == [
        "引継ぎコードと認証番号でセーブデータをダウンロード",
        "ファイルからセーブデータを選択",
        f"{path} からセーブデータを読み込み",
        "adb でデバイスからセーブデータを取得",
        *STARTING_CLASSIC,
    ]


def test_describe_save_reads_only_the_header(io_env: Path) -> None:
    path = write_save_head(150300, datetime.datetime(2025, 1, 2, 3, 4))
    describe = save_management.SaveManagement.describe_save
    assert describe(core.Path(str(path))) == ("2025-01-02 03:04", "15.3.0")
    path.write_bytes((-5).to_bytes(4, "little", signed=True))
    assert describe(core.Path(str(path))) is None
    assert describe(core.Path(str(path) + "-missing")) is None


# -- upload --------------------------------------------------------------------------


@pytest.fixture
def uploads(
    monkeypatch: pytest.MonkeyPatch, server: type[FakeServer]
) -> dict[str, list[Any]]:
    """Records saves and clipboard copies; the clipboard works unless the
    test sets ``uploads["clipboard_ok"] = [False]``."""
    record: dict[str, list[Any]] = {"saved": [], "copied": [], "clipboard_ok": [True]}

    def save_save(save_file: object, check_strict: bool = True) -> None:
        record["saved"].append(check_strict)

    def copy(text: str) -> bool:
        record["copied"].append(text)
        return record["clipboard_ok"][0]

    monkeypatch.setattr(save_management.SaveManagement, "save_save", save_save)
    monkeypatch.setattr(clipboard, "copy", copy)
    return record


def upload(capsys: pytest.CaptureFixture[str]) -> list[str]:
    capsys.readouterr()
    save_management.SaveManagement.save_upload(object())  # type: ignore[arg-type]
    return plain(capsys.readouterr().out).splitlines()


def test_upload_shows_the_codes_in_a_frame_and_copies_the_transfer_code(
    io_env: Path,
    interactive: None,
    uploads: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = upload(capsys)
    assert uploads["saved"] == [False]
    assert uploads["copied"] == ["abc123def"]
    assert lines == [
        "アップロードしました。ゲームで次の引継ぎコードと認証番号を入力してください。",
        "┌" + "─" * 27 + "┐",
        "│  引継ぎコード  abc123def  │",
        "│  認証番号      1234       │",
        "└" + "─" * 27 + "┘",
        "引継ぎコードをクリップボードにコピーしました。",
    ]
    assert len({get_cwidth(line) for line in lines[1:5]}) == 1


def test_upload_without_a_clipboard_tool_only_shows_the_codes(
    io_env: Path,
    interactive: None,
    uploads: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    uploads["clipboard_ok"] = [False]
    lines = upload(capsys)
    assert uploads["copied"] == ["abc123def"]  # tried
    assert lines[-1] == "└" + "─" * 27 + "┘"
    assert not any("クリップボード" in line for line in lines)


def test_upload_frame_falls_back_to_ascii(
    io_env: Path,
    interactive: None,
    uploads: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(save_management, "can_print", lambda text: False)
    lines = upload(capsys)
    assert lines[1] == "+" + "-" * 27 + "+"
    assert lines[2] == "|  引継ぎコード  abc123def  |"


def test_classic_upload_keeps_upstream_output(
    io_env: Path,
    classic: None,
    uploads: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = upload(capsys)
    assert lines == ["", "引継ぎコード: abc123def", "認証番号: 1234", ""]
    assert uploads["copied"] == []


def test_failed_upload_copies_nothing(
    io_env: Path,
    interactive: None,
    uploads: dict[str, list[Any]],
    server: type[FakeServer],
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.codes = None
    lines = upload(capsys)
    assert lines[0].startswith("セーブデータをアップロードできませんでした。")
    assert uploads["copied"] == [] and uploads["saved"] == [False]


def test_codes_box_aligns_wide_labels(io_env: Path) -> None:
    rows = [("<@t>ラベル</>", "x"), ("AB", "<y>")]  # markup in labels, not in codes
    lines = [plain(color.colorize(line)) for line in save_management.codes_box(rows)]
    assert lines == [
        "┌" + "─" * 15 + "┐",
        "│  ラベル  x    │",
        "│  AB      <y>  │",
        "└" + "─" * 15 + "┘",
    ]
    assert len({get_cwidth(line) for line in lines}) == 1


# -- clipboard -----------------------------------------------------------------------


def test_clipboard_commands_per_platform() -> None:
    assert clipboard.commands("darwin", {}) == [["pbcopy"]]
    assert clipboard.commands("win32", {}) == [["clip"]]
    both = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert clipboard.commands("linux", both) == [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]
    assert clipboard.commands("linux", {"DISPLAY": ":0"})[0][0] == "xclip"
    assert clipboard.commands("linux", {}) == []


def test_clipboard_copy_tries_tools_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[tuple[list[str], dict[str, Any]]] = []
    outcome: dict[str, Any] = {"/bin/one": OSError("broken"), "/bin/three": 0}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        runs.append((argv, kwargs))
        result = outcome[argv[0]]
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(argv, result)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    monkeypatch.setattr(
        clipboard, "commands", lambda: [["one"], ["two"], ["three", "-x"]]
    )
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: None if name == "two" else f"/bin/{name}",
    )
    assert REAL_COPY("abc123def") is True
    assert [argv for argv, _ in runs] == [["/bin/one"], ["/bin/three", "-x"]]
    kwargs = runs[-1][1]
    assert kwargs["input"] == b"abc123def"
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["timeout"] == clipboard.TIMEOUT

    outcome["/bin/three"] = 1  # the tool failed
    assert REAL_COPY("abc123def") is False
    outcome["/bin/three"] = subprocess.TimeoutExpired(["three"], 5)
    assert REAL_COPY("abc123def") is False
    monkeypatch.setattr(clipboard, "commands", lambda: [])
    assert REAL_COPY("abc123def") is False


# -- numbers ------------------------------------------------------------------------


def test_number_field_accepts_max(io_env: Path, interactive: None) -> None:
    value = drive(
        lambda: dialog_creator.edit_int_raw(
            "XP", 1234, dialog_creator.MaxValue.i32(99999999)
        ),
        "max" + ENTER,
    )
    assert value == 99999999


def test_number_field_without_game_maximum_rejects_max(
    io_env: Path, interactive: None
) -> None:
    hidden = dialog_creator.MaxValue.none(31).hide_max()
    value = drive(
        lambda: dialog_creator.edit_int_raw("XP", 1234, hidden),
        "max" + ENTER,  # rejected: not a number, input kept
        CTRL_U + "7" + ENTER,
    )
    assert value == 7


def test_multi_edit_defaults_to_all_at_once(io_env: Path, interactive: None) -> None:
    values = [1, 2, 3]
    # check two rows, Enter; Enter on the highlighted "all at once"; one value
    result = drive(
        lambda: dialog_creator.edit_ints_raw(
            "テスト", ["A", "B", "C"], values, dialog_creator.MaxValue.i32(998)
        ),
        TAB + TAB + ENTER,
        ENTER,
        "max" + ENTER,
    )
    assert result == [998, 998, 3]


def test_multi_edit_individually_is_one_up(io_env: Path, interactive: None) -> None:
    result = drive(
        lambda: dialog_creator.edit_ints_raw(
            "テスト", ["A", "B"], [1, 2], dialog_creator.MaxValue.i32(998)
        ),
        TAB + TAB + ENTER,
        UP + ENTER,
        "5" + ENTER,
        "6" + ENTER,
    )
    assert result == [5, 6]


def test_classic_multi_select_error_names_the_range(
    io_env: Path,
    classic: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["9", "1"])
    monkeypatch.setattr(color, "color_input", lambda prompt: next(answers))
    assert dialog_creator.multi_select_entries_raw(["a", "b"], "pick") == [(0, "a")]
    out = plain(capsys.readouterr().out)
    assert "1 から 3 までの数値を入力してください" in out
    assert "built-in" not in out


def test_single_select_default_is_only_a_highlight(
    io_env: Path, interactive: None
) -> None:
    actions = dialog_creator.Actions[str].new().add_new_raw(["a", "b", "c"], str)
    assert (
        drive(lambda: dialog_creator.single_select_raw(actions, "t", 2), ENTER) == "2"
    )
    assert drive(lambda: dialog_creator.single_select_raw(actions, "t"), ENTER) == "0"


# -- file dialog and keys used by upstream code ------------------------------------------


def test_file_dialog_off_never_imports_tkinter(
    io_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core.core_data.config.set(core.ConfigKey.USE_FILE_DIALOG, False)
    imported: list[str] = []
    real_import = builtins.__import__

    def tracking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    dialog = file_dialog.FileDialog()
    assert dialog.tk is None and dialog.filedialog is None
    assert not dialog.use_tk()
    assert "tkinter" not in imported


def test_fixed_locale_keys_exist(io_env: Path) -> None:
    manager = core.core_data.local_manager
    for key in (
        "edit_specific_stages",
        "unknown_map_name",
        "locale_cancelled",
        "cancel",
        "io_enemy_no_name",
        "io_enemy_unknown_name",
    ):
        assert manager.get_key(key, id=3) != key, key
    assert manager.get_key("io_enemy_no_name", id=3) == "名前のない敵 (ID: 3)"
    names = core.EnemyNames.__new__(core.EnemyNames)
    names.names = ["ネコ", ""]
    assert names.get_name(0) == "ネコ"
    assert names.get_name(1) == "名前のない敵 (ID: 1)"
    assert names.get_name(5) == "不明な敵 (ID: 5)"
