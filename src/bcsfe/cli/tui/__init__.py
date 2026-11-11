"""Interactive prompt widgets built on prompt_toolkit.

fzf-style ``select`` / ``multi_select`` lists with live filtering (Japanese
kana-insensitive), a one-key ``confirm`` and validated ``text`` / ``integer``
fields.  They render inline, erase themselves when done and leave one summary
line.  When the terminal is not interactive (see :func:`is_interactive`) the
classic numbered ``input()`` prompts are used instead.  ``None`` always means
the user cancelled.
"""

from bcsfe.cli.tui.base import (
    Choice,
    ExtraKey,
    ListFilter,
    ListResult,
    TuiUnavailable,
    is_interactive,
)
from bcsfe.cli.tui.widgets import confirm, integer, multi_select, pick, select, text

__all__ = [
    "Choice",
    "ExtraKey",
    "ListFilter",
    "ListResult",
    "TuiUnavailable",
    "confirm",
    "integer",
    "is_interactive",
    "multi_select",
    "pick",
    "select",
    "text",
]
