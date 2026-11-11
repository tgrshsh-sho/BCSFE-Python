"""Localised strings of the start header, the home menu and the about feature.

Keys live in ``files/locales/<lang>/fork/home.properties``.  The editor reads
locales from its data folder, which is only refreshed when the version changes
(or with ``--force-migrate``), so missing keys fall back to the English text
below instead of showing the raw key.
"""

from __future__ import annotations

from typing import Any

from bcsfe import core

# Keep in sync with files/locales/en/fork/home.properties.
FALLBACK: dict[str, str] = {
    "home_start_header": (
        "<@t>Battle Cats Save File Editor</> <@s>{version}</>\n"
        "Japanese edition of BCSFE by fieryhenry. "
        'See "{{home_about}}" in the menu for details.\n'
        "<@w>Edits can get you banned or break your save: make a backup first.</>\n"
        "<@w>This tool is free. If you paid for it, you were scammed.</>"
    ),
    "home_about": "About this editor",
    "home_prompt": "Select a feature:",
    "home_hint": "↑↓ Move  Enter Open  Esc Exit  Type to filter",
    "home_summary_save": (
        "<@t>{cc}</> {gv}  Inquiry code <@t>{inquiry_code}</>"
        "  Cats <@t>{cats}</> / {cats_total}"
    ),
    "home_summary_items": (
        "Cat Food <@t>{catfood}</>  XP <@t>{xp}</>  NP <@t>{np}</>"
        "  Rare Tickets <@t>{rare_tickets}</>"
        "  Platinum Tickets <@t>{platinum_tickets}</>"
        "  Legend Tickets <@t>{legend_tickets}</>"
    ),
    "home_heading": "[{name}]",
    "home_favourites": "Frequently used",
}


def _escape(value: str) -> str:
    for char in core.LocalManager.get_special_chars():
        value = value.replace(char, "\\" + char)
    return value


def tr(key: str, **kwargs: Any) -> str:
    """Localised text (editor colour markup) for ``key``; ``kwargs`` are
    escaped, so they are shown literally."""
    try:
        value = core.core_data.local_manager.get_key(key, **kwargs)
    except Exception:  # locale not initialised yet
        value = key
    if value != key:
        return value
    value = FALLBACK.get(key, key)
    while "{{" in value:  # references to other keys
        start = value.index("{{")
        end = value.find("}}", start)
        if end < 0:
            break
        name = value[start + 2 : end]
        value = value[:start] + tr(name) + value[end + 2 :]
    for name, arg in kwargs.items():
        value = value.replace("{" + name + "}", _escape(str(arg)))
    return value
