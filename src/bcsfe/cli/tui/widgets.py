"""Public widget functions.

Each function uses the interactive prompt_toolkit widget when
:func:`is_interactive` is true and otherwise (or when the widget cannot start)
the classic ``input()`` prompt.  ``None`` always means the user cancelled
(Esc or Ctrl-C in the widget, the quit key ``q`` in the classic prompt).

``fallback=False`` raises :class:`TuiUnavailable` instead of running the
classic prompt, for callers that have their own classic code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from bcsfe import core
from bcsfe.cli import color
from bcsfe.cli.tui import classic, strings
from bcsfe.cli.tui.base import Choice, TuiUnavailable, is_interactive

_notice_shown = False


def _choices(choices: Sequence[Choice | str]) -> list[Choice]:
    return [c if isinstance(c, Choice) else Choice(c) for c in choices]


def _failed(error: Exception) -> None:
    """Record why a widget could not run; tell the user once."""
    global _notice_shown
    try:
        core.core_data.logger.log_exception(error, "interactive prompt failed")
    except Exception:
        pass
    if not _notice_shown:
        _notice_shown = True
        try:
            color.color_print(strings.tr("tui_fallback_notice"))
        except Exception:
            pass


def _unavailable(fallback: bool) -> None:
    if not fallback:
        raise TuiUnavailable("interactive prompt not available")


def select(
    title: str,
    choices: Sequence[Choice | str],
    *,
    default: int = 0,
    footer: str | None = None,
    fallback: bool = True,
) -> int | None:
    """Pick one entry; returns its index (into ``choices``) or None.

    fzf-style: typing filters the list (see :class:`~.matching.Matcher`),
    ↑/↓ Ctrl-P/Ctrl-N PageUp/PageDown Home/End move, Enter chooses, Esc
    cancels.  ``default`` is the initially highlighted index, ``footer`` an
    optional extra line (markup) shown under the list.  Returns None at once
    when ``choices`` is empty."""
    items = _choices(choices)
    if not items:
        return None
    if is_interactive():
        try:
            from bcsfe.cli.tui import _apps

            return _apps.select(title, items, default=default, footer=footer)
        except EOFError:
            raise
        except Exception as e:
            _failed(e)
    _unavailable(fallback)
    return classic.select(title, items, footer)


def multi_select(
    title: str,
    choices: Sequence[Choice | str],
    *,
    selected: Iterable[int] = (),
    footer: str | None = None,
    fallback: bool = True,
) -> list[int] | None:
    """Pick any number of entries; returns sorted indexes or None.

    Like :func:`select`, plus: Tab toggles the highlighted row (and moves
    down), Ctrl-A checks every row matching the current query, Ctrl-D clears
    all checks.  Enter returns the checked rows, or just the highlighted row
    when nothing is checked.  Checks survive changing the query.
    ``selected`` are the initially checked indexes."""
    items = _choices(choices)
    if not items:
        return []
    if is_interactive():
        try:
            from bcsfe.cli.tui import _apps

            return _apps.multi_select(title, items, selected=selected, footer=footer)
        except EOFError:
            raise
        except Exception as e:
            _failed(e)
    _unavailable(fallback)
    return classic.multi_select(title, items, footer)


def confirm(
    message: str, *, default: bool = False, fallback: bool = True
) -> bool | None:
    """Yes/no question.  ``y``/``n`` answer at once; ←/→/Tab switch the
    highlighted answer (initially ``default``) and Enter accepts it."""
    if is_interactive():
        try:
            from bcsfe.cli.tui import _apps

            return _apps.confirm(message, default=default)
        except EOFError:
            raise
        except Exception as e:
            _failed(e)
    _unavailable(fallback)
    return classic.confirm(message, default)


def text(
    message: str,
    *,
    default: str = "",
    validate: Callable[[str], str | None] | None = None,
    fallback: bool = True,
) -> str | None:
    """One line of text, pre-filled with ``default``.  ``validate`` returns an
    error message (shown under the field, input kept) or None to accept."""
    if is_interactive():
        try:
            from bcsfe.cli.tui import _apps

            return _apps.text(message, default=default, validate=validate)
        except EOFError:
            raise
        except Exception as e:
            _failed(e)
    _unavailable(fallback)
    return classic.text(message, default, validate)


def integer(
    message: str,
    *,
    minimum: int,
    maximum: int,
    current: int | None = None,
    clamp: bool = False,
    show_range: bool = True,
    current_label: str | None = None,
    fallback: bool = True,
) -> int | None:
    """A whole number in ``minimum..maximum``.

    The field shows the range (unless ``show_range`` is False) and ``current``
    (labelled ``current_label``, default "current").  Empty Enter returns
    ``current`` when it is not None.  Out-of-range numbers are rejected with an
    inline error, or clamped when ``clamp`` is True.  Full-width digits and
    ``1,000`` are accepted."""
    if is_interactive():
        try:
            from bcsfe.cli.tui import _apps

            return _apps.integer(
                message,
                minimum=minimum,
                maximum=maximum,
                current=current,
                clamp=clamp,
                show_range=show_range,
                current_label=current_label,
            )
        except EOFError:
            raise
        except Exception as e:
            _failed(e)
    _unavailable(fallback)
    return classic.integer(message, minimum, maximum, current, clamp)
