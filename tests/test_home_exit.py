"""The exit flow (Main.exit_editor) and the temp save written after every
feature (SaveFile.to_file_thread), without a terminal.

Regression: choosing exit right after a feature sometimes failed with
"Failed to recover save file from temp file" (CantDetectSaveCCError) because
exit_editor read the temp save while to_file_thread was still writing it.
"""

from __future__ import annotations

import builtins
import pathlib
import shutil
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from bcsfe import core
from bcsfe.cli import color, dialog_creator, main, save_management
from bcsfe.core.io.save import FailedToSaveError
from tests.conftest import FIXTURE_SAVE
from tests.test_home import StartCore, core_starter


@pytest.fixture
def start_core(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StartCore]:
    with core_starter(tmp_path_factory, monkeypatch) as start:
        yield start


def make_save(start_core: StartCore) -> tuple[core.SaveFile, pathlib.Path]:
    """The fixture save, loaded from ``<root>/SAVE_DATA`` like ``-i`` does."""
    if not FIXTURE_SAVE.is_file():
        pytest.skip(f"fixture save not found: {FIXTURE_SAVE}")
    root = start_core("ja", name="exit")
    path = root / "SAVE_DATA"
    shutil.copyfile(FIXTURE_SAVE, path)
    save = core.SaveFile(core.Path(str(path)).read())
    save.save_path = core.Path(str(path))
    return save, path


class Prompts:
    """Records printed keys and answers the yes/no questions."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, answer: bool | None) -> None:
        self.answer = answer
        self.printed: list[str] = []
        self.asked: list[str] = []
        self.saved: list[core.SaveFile] = []

        def color_print_key(key: str, *args: Any, **kwargs: Any) -> None:
            self.printed.append(key)

        def yes_no_key(dialog: str, *args: Any, **kwargs: Any) -> bool | None:
            self.asked.append(dialog)
            if dialog == "save_before_exit":
                return self.answer
            return False  # e.g. never upload managed items

        monkeypatch.setattr(color, "color_print_key", color_print_key)
        monkeypatch.setattr(dialog_creator, "yes_no_key", yes_no_key)

    def record_saves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def save_save(save_file: core.SaveFile, check_strict: bool = True) -> None:
            self.saved.append(save_file)

        monkeypatch.setattr(save_management.SaveManagement, "save_save", save_save)


def write_temp(data: bytes) -> pathlib.Path:
    path = pathlib.Path(core.SaveFile.get_temp_path().path)
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------- exit flow


@pytest.mark.parametrize("cancellable", [True, False])
def test_exit_with_the_save_in_memory_does_not_read_the_temp_file(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch, cancellable: bool
) -> None:
    save, _ = make_save(start_core)
    partial = FIXTURE_SAVE.read_bytes()[:100_000]  # a temp save still being written
    write_temp(partial)
    with pytest.raises(core.CantDetectSaveCCError):
        core.SaveFile(core.Data(partial))  # what the old exit_editor tripped over
    save.xp = 12345
    prompts = Prompts(monkeypatch, None)
    prompts.record_saves(monkeypatch)

    if cancellable:
        assert main.Main.exit_editor(save, cancellable=True) is None
    else:
        with pytest.raises(SystemExit):
            main.Main.exit_editor(save)

    assert "save_temp_fail" not in prompts.printed
    assert prompts.printed[:2] == ["checking_for_changes", "changes_found"]
    assert prompts.asked == ["save_before_exit"]
    assert prompts.saved == []


def test_yes_saves_the_save_in_memory_and_leaves(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, path = make_save(start_core)
    write_temp(b"garbage")
    save.xp = 12345
    prompts = Prompts(monkeypatch, True)

    with pytest.raises(SystemExit):
        main.Main.exit_editor(save, cancellable=True)

    assert core.SaveFile(core.Path(str(path)).read()).xp == 12345
    assert prompts.printed[-2:] == ["save_success", "leave"]


def test_no_changes_leave_without_asking(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, path = make_save(start_core)
    path.write_bytes(save.to_data().data)
    prompts = Prompts(monkeypatch, None)

    with pytest.raises(SystemExit):
        main.Main.exit_editor(save, cancellable=True)

    assert prompts.asked == []
    assert prompts.printed == ["checking_for_changes", "no_changes", "leave"]


def test_missing_save_file_counts_as_changes(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, path = make_save(start_core)
    path.unlink()
    prompts = Prompts(monkeypatch, None)

    assert main.Main.exit_editor(save, cancellable=True) is None

    assert prompts.asked == ["save_before_exit"]


def test_cancelled_save_dialog_returns_only_when_cancellable(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, _ = make_save(start_core)
    save.save_path = None  # e.g. loaded from JSON: save_save asks for a path
    prompts = Prompts(monkeypatch, True)
    asked_path: list[bool] = []

    def no_path(save_file: core.SaveFile) -> None:
        asked_path.append(True)

    monkeypatch.setattr(main.Main, "save_save_dialog", staticmethod(no_path))

    assert main.Main.exit_editor(save, cancellable=True) is None
    with pytest.raises(SystemExit):
        main.Main.exit_editor(save)

    assert asked_path == [True, True]
    assert prompts.printed.count("leave") == 1


@pytest.mark.parametrize(
    ("typed", "saves"),
    [("y", True), ("n", False), ("", False), ("q", False), ("yes", False)],
)
def test_classic_prompt_only_y_saves(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch, typed: str, saves: bool
) -> None:
    save, _ = make_save(start_core)
    save.xp = 12345
    monkeypatch.setenv("BCSFE_UI", "classic")
    asked: list[str] = []

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        return typed

    monkeypatch.setattr(builtins, "input", fake_input)
    prompts_saved: list[core.SaveFile] = []
    monkeypatch.setattr(
        save_management.SaveManagement,
        "save_save",
        lambda save_file, check_strict=True: prompts_saved.append(save_file),
    )

    with pytest.raises(SystemExit):
        main.Main.exit_editor(save)

    assert len(asked) == 1 and "保存しますか" in asked[0]
    assert prompts_saved == ([save] if saves else [])


def test_crash_recovery_restores_the_temp_save(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, _ = make_save(start_core)
    save.xp = 4242
    thread = save.to_file_thread(core.SaveFile.get_temp_path())
    assert thread is not None
    thread.join()
    prompts = Prompts(monkeypatch, True)
    prompts.record_saves(monkeypatch)

    with pytest.raises(SystemExit):
        main.Main.exit_editor()

    assert [s.xp for s in prompts.saved] == [4242]


@pytest.mark.parametrize("answer", [None, False])
def test_crash_recovery_keeps_exiting_without_saving(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch, answer: bool | None
) -> None:
    save, _ = make_save(start_core)
    save.xp = 4242
    thread = save.to_file_thread(core.SaveFile.get_temp_path())
    assert thread is not None
    thread.join()
    prompts = Prompts(monkeypatch, answer)
    prompts.record_saves(monkeypatch)

    with pytest.raises(SystemExit):
        main.Main.exit_editor()

    assert prompts.saved == []


def test_crash_recovery_without_a_temp_save(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_save(start_core)
    core.SaveFile.get_temp_path().remove()
    prompts = Prompts(monkeypatch, True)

    with pytest.raises(SystemExit):
        main.Main.exit_editor()

    assert prompts.printed == ["save_temp_not_found", "leave"]


def test_crash_recovery_with_a_broken_temp_save(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_save(start_core)
    write_temp(b"broken")
    prompts = Prompts(monkeypatch, True)

    with pytest.raises(SystemExit):
        main.Main.exit_editor()

    assert prompts.printed == ["save_temp_fail", "leave"]


def test_exit_from_the_load_menu_leaves_at_once(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_save(start_core)
    prompts = Prompts(monkeypatch, True)

    with pytest.raises(SystemExit):
        main.Main.exit_editor(check_temp=False)

    assert prompts.printed == ["leave"]


# --------------------------------------------------------------------- temp save


def test_temp_save_reader_never_sees_a_partly_written_file(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, _ = make_save(start_core)
    temp = core.SaveFile.get_temp_path()
    old_xp = save.xp
    first = save.to_file_thread(temp)
    assert first is not None
    first.join()
    before = pathlib.Path(temp.path).read_bytes()

    started = threading.Event()
    release = threading.Event()

    def slow_to_file(self: core.Data, path: core.Path) -> None:
        with open(path.path, "wb") as f:
            f.write(self.data[: len(self.data) // 2])
            f.flush()
            started.set()
            assert release.wait(10)
            f.write(self.data[len(self.data) // 2 :])

    monkeypatch.setattr(core.Data, "to_file", slow_to_file)
    save.xp = 777
    thread = save.to_file_thread(temp)
    assert thread is not None
    try:
        assert started.wait(10)
        during = pathlib.Path(temp.path).read_bytes()  # what crash recovery reads
        assert during == before
        assert core.SaveFile(core.Data(during)).xp == old_xp
    finally:
        release.set()
        thread.join()

    assert core.SaveFile(temp.read()).xp == 777
    assert not pathlib.Path(temp.path + ".partial").exists()


def test_temp_save_is_a_snapshot_taken_by_the_call(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, _ = make_save(start_core)
    temp = core.SaveFile.get_temp_path()
    start = core.Thread.start
    pending: list[core.Thread] = []
    monkeypatch.setattr(core.Thread, "start", lambda self: pending.append(self))

    save.xp = 100
    save.to_file_thread(temp)
    save.xp = 200  # the next edit, before the background write starts
    start(pending[0])
    pending[0].join()

    assert core.SaveFile(temp.read()).xp == 100


def test_older_temp_snapshot_never_replaces_a_newer_one(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    save, _ = make_save(start_core)
    temp = core.SaveFile.get_temp_path()
    start = core.Thread.start
    pending: list[core.Thread] = []
    monkeypatch.setattr(core.Thread, "start", lambda self: pending.append(self))

    save.xp = 1
    save.to_file_thread(temp)
    save.xp = 2
    save.to_file_thread(temp)
    older, newer = pending
    start(newer)
    newer.join()
    start(older)  # late: must not overwrite the newer snapshot
    older.join()

    assert core.SaveFile(temp.read()).xp == 2


def test_temp_save_error_is_printed_not_raised(
    start_core: StartCore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    save, _ = make_save(start_core)

    def fail() -> core.Data:
        raise FailedToSaveError("cannot serialise")

    monkeypatch.setattr(save, "to_data", fail)

    assert save.to_file_thread(core.SaveFile.get_temp_path()) is None
    assert "cannot serialise" in capsys.readouterr().out
