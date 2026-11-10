"""Pseudo-terminal harness: run a command in a fixed-size PTY and read its screen.

The command runs in a real pseudo-terminal (``pty.fork``, so it gets a
controlling terminal and ``isatty()`` is true), everything it prints is fed
through ``pyte`` and the visible screen can be read back as text rows.  Cursor
position requests (``ESC [ 6 n``, used by prompt_toolkit) are answered, so
inline prompt_toolkit applications behave as in a real terminal.

Library use (pytest)::

    from tests.tui_harness import Terminal

    with Terminal([sys.executable, "-c", "..."], cols=100, rows=30) as term:
        term.wait_for("絞り込み")      # raises TimeoutError with a screen dump
        term.send_text("ネコ")         # any UTF-8 text, including Japanese
        term.send_key("down")          # named special key, see KEYS
        term.send_key("enter")
        print(term.screen_text())      # visible screen, one line per row
        term.wait_exit()

Main methods: ``send`` (raw str/bytes), ``send_text``, ``send_key``,
``paste`` (bracketed paste), ``wait_for(text)``, ``wait_gone(text)``,
``wait_idle(quiet)``, ``wait_exit()``, ``screen_lines()``, ``screen_text()``,
``history_lines()`` (rows scrolled off the top, then the screen) and ``close()``.

Command line::

    .venv/bin/python tests/tui_harness.py [options] --keys '<spec>' -- <command...>

prints the final screen.  ``<spec>`` is a shell-quoted list of tokens, run in
order:

=================  ==========================================================
``text:STR``       type STR (quote it if it contains spaces: ``"text:a b"``)
``key:NAME[*N]``   press a special key N times, e.g. ``key:down*3``; names:
                   enter, esc, tab, s-tab, up, down, left, right, home, end,
                   pageup, pagedown, backspace, delete, space, c-a ... c-z
``paste:STR``      bracketed paste of STR
``raw:ESCAPED``    send bytes given with Python escapes, e.g. ``raw:\\x1b[A``
``wait:TEXT``      wait until TEXT is visible on the screen (``--timeout``)
``gone:TEXT``      wait until TEXT is no longer visible
``idle[:SEC]``     wait until there was no output for SEC seconds (0.3)
``sleep:SEC``      sleep SEC seconds (while still reading output)
``dump[:LABEL]``   print the current screen now (for intermediate states)
``exit[:SEC]``     wait for the command to exit
=================  ==========================================================

Options: ``--cols`` / ``--rows`` (default 100x30), ``--timeout`` (seconds per
wait, default 10), ``--settle`` (idle time before the final dump, default
0.3), ``--env KEY=VALUE`` (repeatable), ``--cwd DIR``, ``--history`` (also
print rows that scrolled off the top), ``--full`` (keep trailing blank rows).

Example::

    .venv/bin/python tests/tui_harness.py \\
        --keys 'wait:Enter text:ねこ key:down dump:filtered key:enter' \\
        -- .venv/bin/python tests/tui_scripts/widget_demo.py select

Exit status: 0 on success, 1 when a wait timed out or the command exited
while waiting (the screen is printed), 2 for a bad key spec (checked before
the command starts).  The command is terminated (SIGTERM, then SIGKILL) when
the harness exits.  Environment defaults: ``TERM=xterm-256color``,
``LANG=ja_JP.UTF-8``, UTF-8 Python I/O.
"""

from __future__ import annotations

import argparse
import codecs
import fcntl
import os
import pty
import select
import shlex
import signal
import struct
import sys
import termios
import time
from collections.abc import Callable, Mapping, Sequence
from types import TracebackType

import pyte
from pyte.screens import Char

KEYS: dict[str, str] = {
    "enter": "\r",
    "return": "\r",
    "esc": "\x1b",
    "escape": "\x1b",
    "tab": "\t",
    "s-tab": "\x1b[Z",
    "backtab": "\x1b[Z",
    "backspace": "\x7f",
    "bs": "\x7f",
    "delete": "\x1b[3~",
    "del": "\x1b[3~",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "pageup": "\x1b[5~",
    "pgup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "pgdn": "\x1b[6~",
    "space": " ",
    "c-space": "\x00",
}
for _i, _letter in enumerate("abcdefghijklmnopqrstuvwxyz", start=1):
    KEYS[f"c-{_letter}"] = chr(_i)

DEFAULT_ENV = {
    "TERM": "xterm-256color",
    "LANG": "ja_JP.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


class _Screen(pyte.HistoryScreen):
    """pyte screen that collects what the terminal would answer (CPR)."""

    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines, history=2000, ratio=0.5)
        self.replies: list[str] = []

    def write_process_input(self, data: str) -> None:
        self.replies.append(data)


def _render_row(row: Mapping[int, Char], columns: int) -> str:
    out: list[str] = []
    skip = False
    for x in range(columns):
        if skip:
            skip = False
            continue
        data = row[x].data
        out.append(data)
        skip = bool(data) and _wide(data[0])
    return "".join(out).rstrip()


def _wide(char: str) -> bool:
    from wcwidth import wcwidth

    return wcwidth(char) == 2


class Terminal:
    """A command running in a pseudo-terminal, observed through pyte."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cols: int = 100,
        rows: int = 30,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.cols = cols
        self.rows = rows
        self.screen = _Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.transcript = bytearray()
        self.exit_status: int | None = None
        self._eof = False
        self._last_output = time.monotonic()

        full_env = dict(os.environ)
        full_env.update(DEFAULT_ENV)
        if env is not None:
            full_env.update(env)
        args = [str(a) for a in argv]
        winsize = struct.pack("HHHH", rows, cols, 0, 0)

        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)
                if cwd is not None:
                    os.chdir(cwd)
                os.execvpe(args[0], args, full_env)
            finally:
                os._exit(127)
        self.pid = pid
        self.fd = fd
        os.set_blocking(fd, False)

    # -- process -----------------------------------------------------------

    def __enter__(self) -> Terminal:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def is_alive(self) -> bool:
        self._reap(block=False)
        return self.exit_status is None

    def _reap(self, block: bool) -> None:
        if self.exit_status is not None:
            return
        try:
            pid, status = os.waitpid(self.pid, 0 if block else os.WNOHANG)
        except ChildProcessError:
            self.exit_status = -1
            return
        if pid == self.pid:
            self.exit_status = os.waitstatus_to_exitcode(status)

    def close(self) -> None:
        """Terminate the command (if still running) and release the PTY."""
        if self.is_alive():
            for sig, grace in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 1.0)):
                try:
                    os.kill(self.pid, sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + grace
                while time.monotonic() < deadline and self.is_alive():
                    self._pump(0.02)
                if not self.is_alive():
                    break
        self._reap(block=True)
        try:
            os.close(self.fd)
        except OSError:
            pass

    # -- output ------------------------------------------------------------

    def _pump(self, timeout: float) -> bool:
        """Read pending output (waiting up to ``timeout``). True if any."""
        if self._eof:
            if timeout > 0:
                time.sleep(timeout)
            return False
        got = False
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
        except (OSError, ValueError):
            ready = []
        while ready:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            except OSError:  # EIO: the child closed the terminal
                self._eof = True
                break
            if not data:
                self._eof = True
                break
            got = True
            self.transcript += data
            self.stream.feed(data)
            self._answer()
        if got:
            self._last_output = time.monotonic()
        return got

    def _answer(self) -> None:
        while self.screen.replies:
            self._write(self.screen.replies.pop(0).encode())

    def screen_lines(self) -> list[str]:
        """Visible rows (right-stripped), always ``rows`` entries."""
        self._pump(0)
        return [_render_row(self.screen.buffer[y], self.cols) for y in range(self.rows)]

    def screen_text(self) -> str:
        """Visible screen as text, trailing blank rows removed."""
        return "\n".join(self.screen_lines()).rstrip("\n")

    def history_lines(self) -> list[str]:
        """Rows that scrolled off the top, followed by the visible rows."""
        self._pump(0)
        top = [_render_row(row, self.cols) for row in self.screen.history.top]
        return top + self.screen_lines()

    @property
    def cursor(self) -> tuple[int, int]:
        return self.screen.cursor.x, self.screen.cursor.y

    # -- input -------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        deadline = time.monotonic() + 10
        while view:
            try:
                n = os.write(self.fd, view)
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError("terminal input is not being read")
                self._pump(0.01)
                continue
            view = view[n:]

    def send(self, data: str | bytes) -> None:
        """Send raw bytes (str is UTF-8 encoded) as if typed."""
        self._write(data.encode("utf-8") if isinstance(data, str) else data)
        self._pump(0.01)

    def send_text(self, text: str) -> None:
        self.send(text)

    def send_key(self, name: str, times: int = 1) -> None:
        try:
            seq = KEYS[name.lower()]
        except KeyError:
            raise ValueError(f"unknown key {name!r}; known: {sorted(KEYS)}") from None
        for _ in range(times):
            self.send(seq)

    def paste(self, text: str) -> None:
        self.send("\x1b[200~" + text + "\x1b[201~")

    # -- waiting -----------------------------------------------------------

    def _wait(self, cond: Callable[[], bool], what: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if cond():
                return
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"timed out after {timeout}s waiting for {what}\n"
                    f"--- screen ---\n{self.screen_text()}\n--- end ---"
                )
            if not self._pump(0.05) and self._eof and not cond():
                raise EOFError(
                    f"command exited while waiting for {what}"
                    f" (status {self.exit_status_now()})\n"
                    f"--- screen ---\n{self.screen_text()}\n--- end ---"
                )

    def exit_status_now(self) -> int | None:
        self._reap(block=False)
        return self.exit_status

    def wait_for(self, text: str, timeout: float = 10.0) -> None:
        """Wait until ``text`` is visible on the screen."""
        self._wait(lambda: text in self.screen_text(), repr(text), timeout)

    def wait_gone(self, text: str, timeout: float = 10.0) -> None:
        """Wait until ``text`` is no longer visible."""
        self._wait(lambda: text not in self.screen_text(), f"{text!r} to go", timeout)

    def wait_idle(self, quiet: float = 0.3, timeout: float = 10.0) -> None:
        """Wait until no output arrived for ``quiet`` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() - self._last_output < quiet:
            if time.monotonic() > deadline:
                return
            self._pump(0.02)
            if self._eof:
                return

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            self._pump(min(left, 0.05))

    def wait_exit(self, timeout: float = 10.0) -> int:
        """Wait for the command to exit and return its exit code."""
        deadline = time.monotonic() + timeout
        while self.is_alive():
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"command still running after {timeout}s\n"
                    f"--- screen ---\n{self.screen_text()}\n--- end ---"
                )
            self._pump(0.05)
        self._pump(0)
        assert self.exit_status is not None
        return self.exit_status


# -- command line --------------------------------------------------------


def _format_screen(term: Terminal, label: str, history: bool, full: bool) -> str:
    lines = term.history_lines() if history else term.screen_lines()
    if not full:
        while lines and not lines[-1]:
            lines.pop()
    status = term.exit_status_now()
    state = "running" if status is None else f"exited {status}"
    x, y = term.cursor
    head = f"=== {label}: {term.cols}x{term.rows}, cursor ({x},{y}), {state} ==="
    return "\n".join([head, *lines, "=== end ==="])


_KINDS = (
    "text",
    "key",
    "paste",
    "raw",
    "wait",
    "gone",
    "idle",
    "sleep",
    "dump",
    "exit",
)
_NEEDS_VALUE = ("key", "wait", "gone", "sleep")


def parse_spec(spec: str) -> list[tuple[str, str]]:
    """Split a key spec into ``(kind, value)`` steps; ValueError if invalid."""
    steps: list[tuple[str, str]] = []
    for token in shlex.split(spec):
        kind, sep, value = token.partition(":")
        kind = kind.lower()
        if kind not in _KINDS or (kind == "text" and not sep):
            raise ValueError(
                f"bad key spec token {token!r} (kinds: {', '.join(_KINDS)})"
            )
        if kind in _NEEDS_VALUE and not value:
            raise ValueError(f"key spec token {token!r} needs a value")
        if kind == "key":
            name, _, count = value.partition("*")
            if name.lower() not in KEYS or (count and not count.isdigit()):
                raise ValueError(f"unknown key in {token!r}; known: {', '.join(KEYS)}")
        if kind in ("idle", "sleep", "exit") and value:
            float(value)  # ValueError if not a number
        steps.append((kind, value))
    return steps


def run_spec(
    term: Terminal,
    spec: str,
    *,
    timeout: float,
    history: bool = False,
    full: bool = False,
    out: Callable[[str], None] = print,
) -> None:
    """Run a key spec (see module docstring) against ``term``."""
    for kind, value in parse_spec(spec):
        if kind == "text":
            term.send_text(value)
        elif kind == "key":
            name, _, count = value.partition("*")
            term.send_key(name, int(count) if count else 1)
        elif kind == "paste":
            term.paste(value)
        elif kind == "raw":
            term.send(codecs.decode(value, "unicode_escape").encode("latin-1"))
        elif kind == "wait":
            term.wait_for(value, timeout)
        elif kind == "gone":
            term.wait_gone(value, timeout)
        elif kind == "idle":
            term.wait_idle(float(value) if value else 0.3, timeout)
        elif kind == "sleep":
            term.sleep(float(value))
        elif kind == "dump":
            term.wait_idle(0.2, timeout)
            out(_format_screen(term, value or "dump", history, full))
        else:  # exit
            term.wait_exit(float(value) if value else timeout)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command in a pseudo-terminal and print its screen."
    )
    parser.add_argument("--keys", default="", help="key spec, see module doc")
    parser.add_argument("--cols", type=int, default=100)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--settle", type=float, default=0.3)
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command (put it after --)")
    try:
        parse_spec(args.keys)
    except ValueError as e:
        parser.error(str(e))
    env: dict[str, str] = {}
    for item in args.env:
        key, sep, value = item.partition("=")
        if not sep:
            parser.error(f"--env needs KEY=VALUE, got {item!r}")
        env[key] = value

    status = 0
    with Terminal(
        command, cols=args.cols, rows=args.rows, env=env, cwd=args.cwd
    ) as term:
        try:
            run_spec(
                term,
                args.keys,
                timeout=args.timeout,
                history=args.history,
                full=args.full,
            )
        except (TimeoutError, EOFError) as e:
            print(f"harness: {e.args[0].splitlines()[0]}", file=sys.stderr)
            status = 1
        term.wait_idle(args.settle, args.timeout)
        print(_format_screen(term, "final", args.history, args.full))
    return status


if __name__ == "__main__":
    sys.exit(main())
