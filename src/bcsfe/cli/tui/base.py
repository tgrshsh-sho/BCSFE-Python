"""Light-weight parts of the widget API (no prompt_toolkit import)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from bcsfe import core

ENV_VAR = "BCSFE_UI"
CLASSIC = "classic"


@dataclass(frozen=True)
class Choice:
    """One row of :func:`select` / :func:`multi_select`.

    ``label`` may contain the editor's colour markup.  ``keywords`` is hidden
    text that also matches the query (e.g. an English alias or an id).
    ``hint`` is shown dimmed at the right of the row (e.g. the current value).
    """

    label: str
    keywords: str = ""
    hint: str = ""
    # A section heading: shown while the query is empty (or when a custom
    # filter returns it), never highlighted, checked or returned.
    heading: bool = False


@dataclass(frozen=True)
class ExtraKey:
    """A key that ends a list widget early (see :func:`~.widgets.pick`).

    ``key`` is a prompt_toolkit key name (``"c-o"``, ``"f2"``); ``label`` is
    shown next to it in the key hints; ``name`` is returned in
    :attr:`ListResult.key`."""

    name: str
    key: str
    label: str


@dataclass(frozen=True)
class ListResult:
    """How a list widget ended (see :func:`~.widgets.pick`).

    ``indexes`` are the chosen rows: for Enter the highlighted row (single
    select) or the checked rows, else the highlighted one (multi select); for
    an extra key the same rows at that moment (may be empty).  ``key`` is the
    :attr:`ExtraKey.name` that ended the list, None for Enter.  ``query`` is
    the text in the query field at that moment."""

    indexes: tuple[int, ...]
    key: str | None = None
    query: str = ""


# Custom filter for the list widgets: query -> indexes of the rows to show,
# in display order (headings included only if returned).
ListFilter = Callable[[str], Sequence[int]]


class TuiUnavailable(RuntimeError):
    """The interactive widget could not run (only raised with ``fallback=False``)."""


def _config_ui_mode() -> str:
    try:
        value = core.core_data.config.config.get(core.ConfigKey.UI_MODE)
    except Exception:  # config not loaded yet
        return ""
    return value.strip().lower() if isinstance(value, str) else ""


def is_interactive() -> bool:
    """True when the interactive widgets should be used.

    That is: stdin and stdout are terminals, ``TERM`` is not ``dumb``, the
    ``ui_mode`` config option is not ``classic`` and the environment variable
    ``BCSFE_UI`` is not ``classic``.
    """
    if os.environ.get(ENV_VAR, "").strip().lower() == CLASSIC:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    if _config_ui_mode() == CLASSIC:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False
