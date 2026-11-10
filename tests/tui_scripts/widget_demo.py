"""Run one tui widget with sample data, for the PTY tests and manual checks.

    .venv/bin/python tests/tui_scripts/widget_demo.py WIDGET [--locale ja]
        [--sandbox DIR] [--rows N]

WIDGET is one of: select, multi, confirm, text, integer, big (2000 rows),
range (dialog_creator.range_multi_input_raw), all (each widget in turn).
After each widget the script prints ``RESULT: <repr>``.  The editor's data,
config and XDG folders are redirected into the sandbox (a new temporary
directory when --sandbox is not given), so the user's real data is never used.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ITEMS: list[tuple[str, str, str]] = [
    ("ネコ缶", "cat food catfood", "45,000"),
    ("XP", "experience", "12,345,678"),
    ("ネコビタンA", "catamin a", "3"),
    ("ネコビタンB", "catamin b", "0"),
    ("ネコビタンC", "catamin c", "12"),
    ("スピードアップ", "speed up", "50"),
    ("トレジャーレーダー", "treasure radar", "8"),
    ("ニャンピュータ", "cat cpu", "120"),
    ("ネコボン", "rich cat", "77"),
    ("おかしなネコ缶", "strange cat food", "1"),
    ("赤マタタビの種", "red catfruit seed", "154"),
    ("青マタタビの種", "blue catfruit seed", "159"),
    ("緑マタタビの種", "green catfruit seed", "177"),
    ("紫獣石", "purple behemoth stone", "225"),
    ("<@q>虹</>マタタビ", "epic catfruit", "165"),
    ("ﾈｺｶﾝ半額チケット", "half-width katakana", "2"),
    ("レアチケット", "rare ticket", "30"),
    ("プラチナチケット", "platinum ticket", "4"),
    ("レジェンドチケット", "legend ticket", "1"),
    ("ねこじゃらし", "hiragana item", "9"),
]


def setup(sandbox: Path, locale: str) -> None:
    for name in ("data", "config", "state", "cache"):
        (sandbox / "xdg" / name).mkdir(parents=True, exist_ok=True)
    os.environ["XDG_DATA_HOME"] = str(sandbox / "xdg" / "data")
    os.environ["XDG_CONFIG_HOME"] = str(sandbox / "xdg" / "config")
    os.environ["XDG_STATE_HOME"] = str(sandbox / "xdg" / "state")
    os.environ["XDG_CACHE_HOME"] = str(sandbox / "xdg" / "cache")

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
    core.set_game_data_path(core.Path(str(sandbox / "game_data")))
    core.core_data.init_data()


def run(widget: str) -> None:
    from bcsfe.cli import dialog_creator, tui

    choices = [tui.Choice(label, kw, hint) for label, kw, hint in ITEMS]
    result: object
    if widget == "select":
        result = tui.select("<@t>アイテム</>を選んでください:", choices)
    elif widget == "multi":
        result = tui.multi_select("編集する<@t>アイテム</>を選んでください:", choices)
    elif widget == "confirm":
        result = tui.confirm("<@w>本当に続けますか?</>", default=False)
    elif widget == "text":
        result = tui.text(
            "名前を入力してください:",
            validate=lambda v: None if v.strip() else "空欄にはできません",
        )
    elif widget == "integer":
        result = tui.integer(
            "<@t>XP</> の値を入力してください:",
            minimum=0,
            maximum=99999999,
            current=1234,
        )
    elif widget == "big":
        big = [tui.Choice(f"アイテム {i:04d}", f"item {i}") for i in range(2000)]
        result = tui.select("2000 件から選択:", big)
    elif widget == "range":
        result = dialog_creator.range_multi_input_raw(
            "ステージ番号 (例: 1 2 5-8):", dialog_creator.MaxValue.always_cap(48), 1
        )
    else:
        raise SystemExit(f"unknown widget {widget!r}")
    print(f"RESULT: {result!r}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("widget")
    parser.add_argument("--locale", default="ja")
    parser.add_argument("--sandbox", default=None)
    args = parser.parse_args()
    sandbox = Path(args.sandbox or tempfile.mkdtemp(prefix="bcsfe-tui-demo-"))
    setup(sandbox, args.locale)
    widgets = (
        ["select", "multi", "confirm", "text", "integer"]
        if args.widget == "all"
        else [args.widget]
    )
    for widget in widgets:
        run(widget)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
