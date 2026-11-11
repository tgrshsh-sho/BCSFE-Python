"""Copy text to the system clipboard with the platform's command line tool.

``pbcopy`` on macOS, ``clip`` on Windows, ``wl-copy`` (Wayland) or ``xclip`` /
``xsel`` (X11) elsewhere.  :func:`copy` tries them in turn and returns False
when none is installed or works.  It never raises and never logs or prints the
text: it is used for transfer codes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping

# Seconds to wait for a tool.  xclip, xsel and wl-copy hand the text to a
# background process and return; output goes to DEVNULL so they cannot hang
# on a pipe kept open by that process.
TIMEOUT = 5.0


def commands(
    platform: str | None = None, environ: Mapping[str, str] | None = None
) -> list[list[str]]:
    """Candidate commands (argv) for ``platform`` (default ``sys.platform``),
    best first.  On Linux and other Unix systems they depend on which display
    server ``environ`` (default ``os.environ``) says is running."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        return [["pbcopy"]]
    if platform in ("win32", "cygwin"):
        return [["clip"]]
    found: list[list[str]] = []
    if environ.get("WAYLAND_DISPLAY"):
        found.append(["wl-copy"])
    if environ.get("DISPLAY"):
        found.append(["xclip", "-selection", "clipboard"])
        found.append(["xsel", "--clipboard", "--input"])
    return found


def copy(text: str) -> bool:
    """Put ``text`` on the clipboard; True when a tool accepted it.

    The text is passed as UTF-8 (``clip`` reads its input in the console code
    page, which is the same for ASCII such as a transfer code)."""
    data = text.encode("utf-8")
    for argv in commands():
        program = shutil.which(argv[0])
        if program is None:
            continue
        try:
            result = subprocess.run(
                [program, *argv[1:]],
                input=data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=TIMEOUT,
                check=False,
            )
        except Exception:  # cannot run, timed out, ...: try the next tool
            continue
        if result.returncode == 0:
            return True
    return False
