"""Fork identity: full version string, migration on a fork revision change, updater."""

from __future__ import annotations

import builtins
import os
import pathlib
import re
import subprocess
import sys
from importlib import resources
from typing import Any, Iterator

import pytest

import bcsfe
from bcsfe import cli, core
from bcsfe.cli import color, dialog_creator

_CORE_GLOBALS = (
    "config_path",
    "print_config_err",
    "log_path",
    "transfer_backup_path",
    "game_data_path",
    "data_dir_path",
)
REPO = pathlib.Path(__file__).resolve().parents[1]
_HOME_VARS = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")


def isolated_dirs(tmp_path: pathlib.Path) -> dict[str, str]:
    """HOME/XDG dirs inside tmp_path, so nothing can reach the real editor data."""
    dirs: dict[str, str] = {}
    for var in _HOME_VARS:
        path = tmp_path / var.lower()
        path.mkdir(exist_ok=True)
        dirs[var] = str(path)
    return dirs


@pytest.fixture
def editor_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pathlib.Path]:
    """Point every editor path (and HOME/XDG) at tmp_path and initialise core_data."""
    for var, value in isolated_dirs(tmp_path).items():
        monkeypatch.setenv(var, value)
    saved_globals = {name: getattr(core, name) for name in _CORE_GLOBALS}
    saved_state = dict(core.core_data.__dict__)
    (tmp_path / "data").mkdir()
    core.set_data_dir_path(core.Path(str(tmp_path / "data")))
    core.set_config_path(core.Path(str(tmp_path / "config.yaml")))
    core.set_log_path(core.Path(str(tmp_path / "bcsfe.log")))
    core.set_game_data_path(core.Path(str(tmp_path / "game_data")))
    core.core_data.init_data()
    try:
        yield tmp_path
    finally:
        for name, value in saved_globals.items():
            setattr(core, name, value)
        core.core_data.__dict__.clear()
        core.core_data.__dict__.update(saved_state)


def record_prints(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    printed: list[tuple[str, dict[str, Any]]] = []

    def color_print_key(
        key: str, end: str = "\n", escape: bool = True, **kwargs: Any
    ) -> None:
        printed.append((key, kwargs))

    monkeypatch.setattr(color, "color_print_key", color_print_key)
    return printed


# ------------------------------------------------------------------ version string


def test_full_version_is_base_plus_fork_revision() -> None:
    assert bcsfe.get_full_version() == f"{bcsfe.__version__}+{bcsfe.__fork__}"
    # the base keeps upstream's format: packaging and exported saves rely on it
    assert re.fullmatch(r"\d+\.\d+\.\d+(b\d+)?", bcsfe.__version__)
    assert re.fullmatch(r"jp\.\d+", bcsfe.__fork__)


def test_version_flag_prints_full_version(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bcsfe", "--version"],
        env={**os.environ, **isolated_dirs(tmp_path)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == bcsfe.get_full_version()


def test_start_up_local_version_line_shows_full_version(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed = record_prints(monkeypatch)

    cli.main.Main().print_locale_version()

    assert printed == [("local_version", {"local_version": bcsfe.get_full_version()})]


# ------------------------------------------------------------------------ migrate


def test_migrate_copies_files_again_for_a_new_fork_revision(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bcsfe.__main__ as entry  # guarded: importing does not start the editor

    data = editor_env / "data"
    version_txt = data / "version.txt"
    version_txt.write_text("3.6.0")  # what the stock editor leaves behind
    fork_locale = data / "locales" / "en" / "fork" / "game_data.properties"
    packaged = resources.files("bcsfe").joinpath(
        "files/locales/en/fork/game_data.properties"
    )

    entry.migrate(False)

    assert version_txt.read_text() == bcsfe.get_full_version()
    assert fork_locale.read_bytes() == packaged.read_bytes()

    fork_locale.write_text("edited by the user")
    entry.migrate(False)  # same revision: nothing is copied

    assert fork_locale.read_text() == "edited by the user"

    monkeypatch.setattr(bcsfe, "__fork__", "jp.999")
    entry.migrate(False)  # next fork revision: files/ is copied again

    assert fork_locale.read_bytes() == packaged.read_bytes()
    assert version_txt.read_text() == f"{bcsfe.__version__}+jp.999"


def test_migrate_on_a_fresh_data_dir(editor_env: pathlib.Path) -> None:
    import bcsfe.__main__ as entry

    entry.migrate(False)

    data = editor_env / "data"
    assert (data / "version.txt").read_text() == bcsfe.get_full_version()
    assert (data / "locales" / "ja" / "fork" / "updater.properties").is_file()
    assert (data / "themes" / "default.json").is_file()


def _start_with(
    editor_env: pathlib.Path, version_txt: str | None, locale: str
) -> str | None:
    """Mimic a start: data folder last used by ``version_txt``, config with ``locale``."""
    import bcsfe.__main__ as entry

    data = editor_env / "data"
    if version_txt is not None:
        (data / "version.txt").write_text(version_txt)
    (editor_env / "config.yaml").write_text(f"locale: {locale}\n")
    previous = entry.migrate(False)
    core.core_data.init_data()
    entry.apply_fork_defaults(previous)
    return previous


def _config_locale(editor_env: pathlib.Path) -> str:
    import yaml

    return yaml.safe_load((editor_env / "config.yaml").read_text())["locale"]


def test_first_fork_start_switches_stock_english_to_japanese(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed = record_prints(monkeypatch)

    previous = _start_with(editor_env, "3.6.0", "en")

    assert previous == "3.6.0"
    assert core.core_data.config.get_str(core.ConfigKey.LOCALE) == "ja"
    assert core.core_data.local_manager.locale == "ja"
    assert _config_locale(editor_env) == "ja"
    assert [key for key, _ in printed] == ["fork_locale_switched"]
    message = core.core_data.local_manager.get_key("fork_locale_switched")
    assert "日本語" in message and "{{" not in message


def test_language_chosen_on_the_fork_is_kept(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed = record_prints(monkeypatch)

    _start_with(editor_env, bcsfe.get_full_version(), "en")

    assert _config_locale(editor_env) == "en"
    assert printed == []


@pytest.mark.parametrize("locale", ["tw", "vi", "ja"])
def test_other_stock_languages_are_kept(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    printed = record_prints(monkeypatch)

    _start_with(editor_env, "3.6.0", locale)

    assert _config_locale(editor_env) == locale
    assert printed == []


def test_new_data_folder_uses_the_config_as_is(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed = record_prints(monkeypatch)

    previous = _start_with(editor_env, None, "en")

    assert previous is None
    assert _config_locale(editor_env) == "en"
    assert printed == []


def test_japanese_is_the_default_locale() -> None:
    assert core.Config.get_defaults()[core.ConfigKey.LOCALE] == "ja"


# ------------------------------------------------------------------------ updater


class FakePypiResponse:
    def __init__(self, releases: list[str]) -> None:
        self._json = {"releases": {version: [] for version in releases}}

    def json(self) -> dict[str, Any]:
        return self._json


@pytest.fixture
def no_install(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail on any prompt, process or pip call; return the URLs requested."""
    requested: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"must not be called: {args!r}")

    for module, name in (
        (subprocess, "run"),
        (subprocess, "Popen"),
        (subprocess, "call"),
        (subprocess, "check_call"),
        (subprocess, "check_output"),
        (os, "system"),
        (builtins, "input"),
        (dialog_creator, "yes_no_key"),
        (core.Command, "run"),
        (core.Path, "run"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    return requested


def serve_pypi(
    monkeypatch: pytest.MonkeyPatch, requested: list[str], releases: list[str] | None
) -> None:
    def get(handler: core.RequestHandler, *args: Any, **kwargs: Any) -> Any:
        requested.append(handler.url)
        return None if releases is None else FakePypiResponse(releases)

    monkeypatch.setattr(core.RequestHandler, "get", get)


def test_newer_upstream_release_is_only_reported(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_install: list[str]
) -> None:
    serve_pypi(monkeypatch, no_install, ["3.5.0", bcsfe.__version__, "99.0.0"])
    printed = record_prints(monkeypatch)

    cli.main.Main.check_update()  # no prompt, no pip, and no sys.exit()

    assert no_install == ["https://pypi.org/pypi/bcsfe/json"]
    assert printed == [
        (
            "version_line",
            {"local_version": bcsfe.get_full_version(), "latest_version": "99.0.0"},
        ),
        (
            "fork_upstream_update_available",
            {"latest_version": "99.0.0", "base_version": bcsfe.__version__},
        ),
    ]
    assert not hasattr(core.Updater, "update")  # the pip code path is gone


def test_no_notice_when_upstream_is_not_newer(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_install: list[str]
) -> None:
    serve_pypi(monkeypatch, no_install, ["3.5.0", bcsfe.__version__])
    printed = record_prints(monkeypatch)

    cli.main.Main.check_update()

    assert [key for key, _ in printed] == ["version_line", "no_update_required"]


def test_network_failure_keeps_todays_message(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_install: list[str]
) -> None:
    serve_pypi(monkeypatch, no_install, None)
    printed = record_prints(monkeypatch)

    cli.main.Main.check_update()

    assert printed == [("update_check_fail", {})]


def test_updater_versions() -> None:
    updater = core.Updater()

    assert updater.get_local_version() == bcsfe.get_full_version()
    assert updater.get_base_version() == bcsfe.__version__
    assert updater.is_upstream_newer("99.0.0")
    assert not updater.is_upstream_newer(bcsfe.__version__)


def test_fork_locale_keys(editor_env: pathlib.Path) -> None:
    packaged = resources.files("bcsfe").joinpath("files")
    bcsfe.copy_to_data_dir(packaged, packaged)
    en: dict[str, tuple[str, str]] = {}
    ja: dict[str, tuple[str, str]] = {}
    for name in ("fork/updater", "fork/migration"):
        en.update(core.locale_handler.PropertySet("en", name).properties)
        ja.update(core.locale_handler.PropertySet("ja", name).properties)

    pattern = re.compile(r"[\"'](fork_[a-z0-9_]+)[\"']")
    used = {
        key
        for path in (REPO / "src" / "bcsfe").rglob("*.py")
        for key in pattern.findall(path.read_text(encoding="utf-8"))
    }
    assert used and used <= set(en) == set(ja)
    for key in en:
        assert set(core.LocalManager.get_kwarg_keys(en[key][0])) == set(
            core.LocalManager.get_kwarg_keys(ja[key][0])
        )
