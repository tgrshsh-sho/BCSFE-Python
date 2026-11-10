"""Light-weight parts of the widget API (no prompt_toolkit import)."""

from __future__ import annotations

import os
import sys
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
