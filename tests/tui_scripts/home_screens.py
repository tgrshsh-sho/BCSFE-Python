"""Print the screens of the start header, the home menu, the exit question and
the about feature, from the real editor in a 100x30 pseudo-terminal.

    .venv/bin/python tests/tui_scripts/home_screens.py [--locale ja] [--keep DIR]

A sandbox is created in a new temporary folder (or DIR): private data, config
and XDG folders, copies of the fixture save and game data (see
tests/conftest.py) and an unroutable proxy, so the user's real data and the
game servers are never touched.  Nothing is saved: the run ends with "n" at
the save question.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.conftest import (  # noqa: E402
    FIXTURE_GAME_DATA,
    FIXTURE_SAVE,
    Sandbox,
    sandbox_env,
)
from tests.tui_harness import Terminal  # noqa: E402

HOME = {"ja": "【よく使う】", "en": "[Frequently used]"}
QUESTION = {"ja": "保存しますか", "en": "Save latest changes"}
ABOUT = {"ja": "このエディターについて", "en": "About this editor"}
TIMEOUT = 60.0


def dump(term: Terminal, label: str, lines: list[str] | None = None) -> None:
    term.wait_idle(0.3)
    rows = term.screen_lines() if lines is None else lines
    while rows and not rows[-1]:
        rows = rows[:-1]
    print(f"=== {label}: {term.cols}x{term.rows} ===")
    print("\n".join(rows))
    print("=== end ===\n", flush=True)


def make_sandbox(root: Path, locale: str) -> Sandbox:
    if not FIXTURE_SAVE.is_file() or not FIXTURE_GAME_DATA.is_dir():
        raise SystemExit(f"fixtures not found: {FIXTURE_SAVE}, {FIXTURE_GAME_DATA}")
    shutil.copytree(FIXTURE_GAME_DATA, root / "game_data")
    shutil.copyfile(FIXTURE_SAVE, root / "SAVE_DATA")
    sandbox = Sandbox(root, sandbox_env(root), root / "game_data")
    sandbox.write_config(locale=locale)
    return sandbox


def start(sandbox: Sandbox, locale: str) -> Terminal:
    term = Terminal(
        sandbox.editor_command(sandbox.root / "SAVE_DATA"),
        cols=100,
        rows=30,
        env=sandbox.env,
        cwd=str(REPO),
    )
    term.wait_for(HOME[locale], TIMEOUT)
    return term


def leave_without_saving(term: Terminal, locale: str) -> None:
    term.send_key("esc")
    term.sleep(1.0)
    if QUESTION[locale] in term.screen_text():
        term.send_text("n")
    term.wait_exit(TIMEOUT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locale", default="ja", choices=sorted(HOME))
    parser.add_argument("--keep", default=None, help="sandbox folder to create")
    args = parser.parse_args()
    locale: str = args.locale
    if args.keep:
        root = Path(args.keep)
        root.mkdir(parents=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="bcsfe-home-screens-"))
    sandbox = make_sandbox(root, locale)
    home = HOME[locale]

    with start(sandbox, locale) as term:
        dump(term, "start header and home")
        term.send_text("xp")
        term.wait_gone(home, TIMEOUT)
        dump(term, "home filtered by xp")
        term.send_key("enter")
        term.wait_for("99,999,999", TIMEOUT)
        term.send_text("12345")
        term.send_key("enter")
        term.wait_for(home, TIMEOUT)
        dump(term, "home after the XP edit")
        term.send_key("esc")
        term.wait_for(QUESTION[locale], TIMEOUT)
        dump(term, "exit question")
        term.send_key("esc")
        term.wait_for(home, TIMEOUT)
        dump(term, "exit question cancelled: back at the home")
        term.send_text("about")
        term.wait_gone(home, TIMEOUT)
        term.send_key("enter")
        term.wait_for(home, TIMEOUT)
        term.wait_idle(0.3)
        lines = term.history_lines()
        chosen = [
            i
            for i, line in enumerate(lines)
            if line.startswith("✔") and line.endswith(ABOUT[locale])
        ]
        dump(term, "about (scrollback)", lines[chosen[-1] :])
        leave_without_saving(term, locale)

    with start(sandbox, locale) as term:
        dump(term, "second launch: frequently used")
        leave_without_saving(term, locale)

    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
