"""Shared pytest fixtures: sandbox folders, a private copy of the fixture save
and game data, and an environment that keeps the editor away from the user's
real data folders and from the network.

The fixture save and game data are looked up in ``$BCSFE_FIXTURES`` or
``<repo>/../fixtures`` (``jp-16.0.0.save`` and ``game_data/``); tests that
need them are skipped when they are missing.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # the harness needs pyte and a POSIX pty: import lazily
    from tests.tui_harness import Terminal

REPO = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
FIXTURES = Path(os.environ.get("BCSFE_FIXTURES", str(REPO.parent / "fixtures")))
FIXTURE_SAVE = FIXTURES / "jp-16.0.0.save"
FIXTURE_GAME_DATA = FIXTURES / "game_data"
WIDGET_DEMO = REPO / "tests" / "tui_scripts" / "widget_demo.py"

# Unroutable proxy: any accidental request to the game servers fails at once.
_NO_NETWORK = "http://127.0.0.1:9"


@dataclass
class Sandbox:
    """Temporary home for one editor run."""

    root: Path
    env: dict[str, str]
    game_data: Path

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    def write_config(self, **values: str) -> None:
        """Set config values, keeping the ones written before (the editor fills
        in defaults for every key that is missing)."""
        current: dict[str, str] = {}
        if self.config_path.is_file():
            for line in self.config_path.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition(": ")
                if sep:
                    current[key] = value
        current.update(values)
        lines = [f"{key}: {value}" for key, value in current.items()]
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def editor_command(self, save: Path) -> list[str]:
        return [
            PYTHON,
            "-m",
            "bcsfe",
            "-i",
            str(save),
            "--data-dir",
            str(self.data_dir),
            "--config-path",
            str(self.config_path),
            "--game-data-dir",
            str(self.game_data),
            "--log-path",
            str(self.root / "bcsfe.log"),
        ]

    def demo_command(self, widget: str, locale: str = "ja") -> list[str]:
        return [
            PYTHON,
            str(WIDGET_DEMO),
            widget,
            "--locale",
            locale,
            "--sandbox",
            str(self.root),
        ]


def sandbox_env(root: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in ("DATA", "CONFIG", "STATE", "CACHE"):
        folder = root / "xdg" / name.lower()
        folder.mkdir(parents=True, exist_ok=True)
        env[f"XDG_{name}_HOME"] = str(folder)
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        env[var] = _NO_NETWORK
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    env["BCSFE_UI"] = "auto"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@pytest.fixture(scope="session")
def fixture_game_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A private copy of the fixture game data (the editor may delete versions)."""
    if not FIXTURE_GAME_DATA.is_dir():
        pytest.skip(f"fixture game data not found: {FIXTURE_GAME_DATA}")
    target = tmp_path_factory.mktemp("game_data_copy") / "game_data"
    shutil.copytree(FIXTURE_GAME_DATA, target)
    return target


def _sandbox_root(factory: pytest.TempPathFactory) -> Path:
    # Not tmp_path: its name contains the test name, and the editor's feature
    # search also matches the save path shown in a menu entry.
    return factory.mktemp("sandbox")


@pytest.fixture
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Sandbox:
    """Sandbox folders and environment, without game data."""
    root = _sandbox_root(tmp_path_factory)
    return Sandbox(root, sandbox_env(root), root / "game_data")


@pytest.fixture
def editor_sandbox(
    tmp_path_factory: pytest.TempPathFactory, fixture_game_data: Path
) -> Sandbox:
    """Sandbox with the fixture game data, a copy of the fixture save at
    ``<root>/SAVE_DATA`` and a config pinned to ``locale: en``."""
    if not FIXTURE_SAVE.is_file():
        pytest.skip(f"fixture save not found: {FIXTURE_SAVE}")
    root = _sandbox_root(tmp_path_factory)
    shutil.copyfile(FIXTURE_SAVE, root / "SAVE_DATA")
    sandbox = Sandbox(root, sandbox_env(root), fixture_game_data)
    # The fork defaults to Japanese; tests that assert upstream's English
    # wording pin it here, others call write_config(locale="ja").
    sandbox.write_config(locale="en")
    return sandbox


@pytest.fixture
def terminal() -> Iterator[Callable[..., Terminal]]:
    """Factory starting commands in a 100x30 pseudo-terminal; all are closed
    after the test.  Skips when pyte or a POSIX pty is not available."""
    pytest.importorskip("pyte")
    pytest.importorskip("termios")
    from tests.tui_harness import Terminal

    started: list[Terminal] = []

    def start(
        argv: Sequence[str],
        env: Mapping[str, str] | None = None,
        cols: int = 100,
        rows: int = 30,
    ) -> Terminal:
        term = Terminal(argv, cols=cols, rows=rows, env=env, cwd=str(REPO))
        started.append(term)
        return term

    yield start
    for term in started:
        term.close()
