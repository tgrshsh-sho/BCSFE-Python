"""Localised strings for the widgets.

Keys live in ``files/locales/<lang>/fork/tui.properties``.  The editor reads
locales from its data folder, which is only refreshed when the version changes
(or with ``--force-migrate``), so missing keys fall back to the English text
below instead of showing the raw key.
"""

from __future__ import annotations

from typing import Any

from bcsfe import core

FALLBACK: dict[str, str] = {
    "tui_hint_select": "↑↓ move  Enter choose  Esc back  type to filter",
    "tui_hint_multi": "Tab check/uncheck  Ctrl-A check all shown  Ctrl-D uncheck all",
    "tui_hint_confirm": "y yes  n no  ←→ switch  Enter choose  Esc back",
    "tui_hint_input": "Enter accept  Esc back",
    "tui_hint_keep": "Empty Enter keeps {value}",
    "tui_counter": "{shown} / {total}",
    "tui_selected_count": "{count} checked",
    "tui_no_match": "No matches",
    "tui_yes": "Yes",
    "tui_no": "No",
    "tui_range": "range {min} - {max}",
    "tui_current_label": "current",
    "tui_default_label": "default",
    "tui_error_not_number": "Please enter a whole number",
    "tui_error_range": "Please enter a number from {min} to {max}",
    "tui_error_empty": "Please enter a value",
    "tui_cancelled": "(cancelled)",
    "tui_empty_value": "(empty)",
    "tui_summary_many": "{count} items: {items}, ...",
    "tui_fallback_notice": (
        "<@w>The interactive prompt could not start, "
        "using the classic prompt instead.</>"
    ),
    "tui_ui_mode_auto": "Auto (interactive prompts when run in a terminal)",
    "tui_ui_mode_classic": "Classic (always type numbers)",
    "tui_ui_mode_dialog": "Select the prompt style:",
}


def _escape(value: str) -> str:
    for char in ("<", ">", "/"):
        value = value.replace(char, "\\" + char)
    return value


def tr(key: str, **kwargs: Any) -> str:
    """Localised text (editor colour markup) for ``key``.

    Values of ``kwargs`` are escaped, so they are shown literally."""
    try:
        value = core.core_data.local_manager.get_key(key, **kwargs)
    except Exception:  # locale not initialised yet
        value = key
    if value != key:
        return value
    value = FALLBACK.get(key, key)
    for name, arg in kwargs.items():
        value = value.replace("{" + name + "}", _escape(str(arg)))
    return value


def localize_or(key: str, default: str) -> str:
    """An existing editor key (e.g. ``quit_key``), or ``default`` if missing."""
    try:
        value = core.core_data.local_manager.get_key(key)
    except Exception:
        return default
    return default if value == key else value
