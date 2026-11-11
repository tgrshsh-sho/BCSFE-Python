"""Run the cat picker or the level input on a save, for PTY tests and manual checks.

    .venv/bin/python tests/tui_scripts/cat_picker_demo.py MODE --save FILE
        --game-data DIR [--locale ja] [--sandbox DIR]

MODE is ``pick`` (the picker; Ctrl-O prints ``MORE`` and adds cats 0 and 1)
or ``upgrade`` (the level input for 3 cats, maximum 50+70).  The script
prints ``READY`` before the rows are built and ``RESULT: <repr>`` at the end
(cat ids, or ``(base, plus, should_exit)``).  The save is copied and the
editor's data, config and XDG folders are redirected into the sandbox (a new
temporary directory when --sandbox is not given), so the user's real data is
never used.  Run it with an unroutable HTTPS_PROXY (the PTY tests do).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def setup(sandbox: Path, locale: str, game_data: Path) -> None:
    for name in ("data", "config", "state", "cache"):
        (sandbox / "xdg" / name).mkdir(parents=True, exist_ok=True)
        os.environ[f"XDG_{name.upper()}_HOME"] = str(sandbox / "xdg" / name)

    import bcsfe
    from bcsfe import core

    files = Path(bcsfe.__file__).parent / "files"
    shutil.copytree(files, sandbox / "data", dirs_exist_ok=True)
    config = sandbox / "config.yaml"
    if not config.exists():
        config.write_text(f"locale: {locale}\n", encoding="utf-8")
    core.set_config_path(core.Path(str(config)))
    core.set_data_dir_path(core.Path(str(sandbox / "data")))
    core.set_log_path(core.Path(str(sandbox / "bcsfe.log")))
    core.set_game_data_path(core.Path(str(game_data)))
    core.core_data.init_data()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("pick", "upgrade"))
    parser.add_argument("--save", required=True)
    parser.add_argument("--game-data", required=True)
    parser.add_argument("--locale", default="ja")
    parser.add_argument("--sandbox", default=None)
    args = parser.parse_args()
    sandbox = Path(args.sandbox or tempfile.mkdtemp(prefix="bcsfe-cat-demo-"))
    setup(sandbox, args.locale, Path(args.game_data))

    from bcsfe import core
    from bcsfe.cli import cat_picker

    save_copy = sandbox / "SAVE_DATA"
    shutil.copyfile(args.save, save_copy)
    save = core.SaveFile(core.Data.from_file(core.Path(str(save_copy))))
    print("READY", flush=True)
    if args.mode == "pick":

        def more() -> list[core.Cat] | None:
            print("MORE", flush=True)
            return save.cats.cats[:2]

        cats = cat_picker.pick_cats(save, (), more)
        result: object = None if cats is None else [cat.id for cat in cats]
    else:
        upgrade, should_exit = cat_picker.upgrade_input(49, 70, 3)
        result = (
            None if upgrade is None else upgrade.base,
            None if upgrade is None else upgrade.plus,
            should_exit,
        )
    print(f"RESULT: {result!r}", flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
