"""Run the save download / upload flows against a fake game server, for the PTY
tests and screen dumps.

    .venv/bin/python tests/tui_scripts/io_demo.py FLOW [--locale ja]
        [--sandbox DIR] [--save PATH]

FLOW is one of:

- ``download``: ServerCLI.download_save (codes, region, result line)
- ``upload``: SaveManagement.save_upload (framed codes, clipboard line)
- ``start``: SaveManagement.select_save(True), the start menu; choosing the
  download continues with the fake download and loads the save

After the flow the script prints ``RESULT: <repr>`` (and ``CLIPBOARD: <text>``
for what would have been copied).  Nothing leaves the machine:
``core.ServerHandler`` is a fake that returns the save given with ``--save``
(else a few bytes) and the codes ``abc123def`` / ``1234``,
``core.RequestHandler`` refuses to send, the proxies point at an unroutable
address and the clipboard is a fake.  HOME and the editor's data, config, log
and XDG folders are redirected into the sandbox (a new temporary directory when
--sandbox is not given), so the user's real data is never used.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

FAKE_CODES = ("abc123def", "1234")
NO_NETWORK = "http://127.0.0.1:9"


def setup(sandbox: Path, locale: str) -> None:
    (sandbox / "home").mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(sandbox / "home")
    for name in ("data", "config", "state", "cache"):
        (sandbox / "xdg" / name).mkdir(parents=True, exist_ok=True)
        os.environ[f"XDG_{name.upper()}_HOME"] = str(sandbox / "xdg" / name)
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        os.environ[var] = NO_NETWORK
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ""

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


def install_fakes(save: Path | None, copied: list[str]) -> None:
    from bcsfe import core
    from bcsfe.cli import clipboard

    content = save.read_bytes() if save is not None else b"not a real save"

    class DownloadedSave:
        def to_file(self, path: core.Path) -> None:
            Path(path.path).parent.mkdir(parents=True, exist_ok=True)
            Path(path.path).write_bytes(content)

    class FakeServer:
        def __init__(self, save_file: Any, print: bool = True) -> None:
            self.save_file = save_file

        @staticmethod
        def from_codes(*args: Any, **kwargs: Any) -> tuple[FakeServer, None]:
            return FakeServer(DownloadedSave()), None

        def get_codes(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
            return FAKE_CODES

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise SystemExit("io_demo: a real request was about to be sent")

    def fake_copy(text: str) -> bool:
        copied.append(text)
        return True

    core.ServerHandler = FakeServer  # type: ignore[misc]
    core.RequestHandler.get = refuse  # type: ignore[method-assign]
    core.RequestHandler.post = refuse  # type: ignore[method-assign]
    clipboard.copy = fake_copy


class UploadSave:
    """What save_upload uses of the save: no managed items, a path."""

    def __init__(self, path: Path) -> None:
        from bcsfe import core

        self.save_path = core.Path(str(path))

    def get_strings(self, identifier: str) -> list[str]:
        return []

    def to_file(self, path: Any) -> None:
        Path(path.path).parent.mkdir(parents=True, exist_ok=True)
        Path(path.path).write_bytes(b"uploaded")


def run(flow: str, sandbox: Path) -> object:
    from bcsfe.cli import save_management, server_cli

    if flow == "download":
        return server_cli.ServerCLI().download_save()
    if flow == "upload":
        save = UploadSave(sandbox / "data" / "saves" / "SAVE_DATA")
        save_management.SaveManagement.save_upload(save)  # type: ignore[arg-type]
        return None
    if flow == "start":
        save_file, stop = save_management.SaveManagement.select_save(True)
        return (None if save_file is None else save_file.game_version.to_string(), stop)
    raise SystemExit(f"unknown flow {flow!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("flow", choices=("download", "upload", "start"))
    parser.add_argument("--locale", default="ja")
    parser.add_argument("--sandbox", default=None)
    parser.add_argument("--save", default=None, help="save returned by the download")
    args = parser.parse_args()
    sandbox = Path(args.sandbox or tempfile.mkdtemp(prefix="bcsfe-io-demo-"))
    setup(sandbox, args.locale)
    copied: list[str] = []
    install_fakes(Path(args.save) if args.save else None, copied)
    result = run(args.flow, sandbox)
    print(f"RESULT: {result!r}", flush=True)
    for text in copied:
        print(f"CLIPBOARD: {text}", flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
