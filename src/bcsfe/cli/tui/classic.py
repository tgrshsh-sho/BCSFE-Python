"""Plain ``input()`` versions of the widgets.

Used when the terminal is not interactive (piped stdin, ``ui_mode: classic``,
``BCSFE_UI=classic``) or when a widget fails to start.  They follow the
editor's classic conventions: numbered lists, the ``quit_key`` (``q``) to go
back, ``yes_key`` (``y``) for yes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from bcsfe.cli import color
from bcsfe.cli.tui import matching, strings
from bcsfe.cli.tui.base import Choice


def _quit_key() -> str:
    return strings.localize_or("quit_key", "q")


def _invalid(minimum: int, maximum: int) -> None:
    text = strings.localize_or(
        "invalid_input_int",
        "<@e>Invalid input. Please enter a number between {min} and {max}</>",
    )
    color.color_print(
        text.replace("{min}", str(minimum)).replace("{max}", str(maximum))
    )


def _print_choices(choices: Sequence[Choice], extra: Sequence[str] = ()) -> None:
    lines: list[str] = []
    for i, choice in enumerate(choices):
        hint = f"  <@s>{choice.hint}</>" if choice.hint else ""
        lines.append(f" {i + 1}. <@t>{choice.label}</>{hint}")
    for j, label in enumerate(extra, start=len(choices)):
        lines.append(f" {j + 1}. <@t>{label}</>")
    color.color_print("\n".join(lines))


def select(title: str, choices: Sequence[Choice], footer: str | None) -> int | None:
    quit_key = _quit_key()
    while True:
        _print_choices(choices)
        if footer:
            color.color_print(footer)
        inp = color.color_input(title)
        if inp == quit_key:
            return None
        text = inp.strip()
        if text.isdigit():
            index = int(text) - 1
            if 0 <= index < len(choices):
                return index
        else:
            wanted = text.lower()
            for i, choice in enumerate(choices):
                if matching.strip_markup(choice.label).strip().lower() == wanted:
                    return i
        _invalid(1, len(choices))


def multi_select(
    title: str, choices: Sequence[Choice], footer: str | None
) -> list[int] | None:
    quit_key = _quit_key()
    all_label = strings.localize_or("all", "All")
    total = len(choices)
    while True:
        _print_choices(choices, [all_label])
        if footer:
            color.color_print(footer)
        inp = color.color_input(title)
        if inp == quit_key:
            return None
        picked: set[int] = set()
        ok = True
        for part in inp.split():
            first, sep, last = part.partition("-")
            if part.isdigit() and int(part) == total + 1:
                picked.update(range(total))
            elif part.isdigit() and 1 <= int(part) <= total:
                picked.add(int(part) - 1)
            elif sep and first.isdigit() and last.isdigit():
                lo, hi = int(first), int(last)
                if not 1 <= lo <= hi <= total:
                    ok = False
                    break
                picked.update(range(lo - 1, hi))
            else:
                ok = False
                break
        if ok:
            return sorted(picked)
        _invalid(1, total + 1)


def confirm(message: str, default: bool) -> bool | None:
    inp = color.color_input(message)
    if inp == _quit_key():
        return None
    answer = inp.strip().lower()
    if not answer:
        return default
    return answer == strings.localize_or("yes_key", "y").strip().lower()


def text(
    message: str, default: str, validate: Callable[[str], str | None] | None
) -> str | None:
    quit_key = _quit_key()
    while True:
        inp = color.color_input(message)
        if inp == quit_key:
            return None
        if inp == f"\\{quit_key}":
            inp = quit_key
        if not inp and default:
            inp = default
        error = validate(inp) if validate is not None else None
        if error is None:
            return inp
        color.color_print(error)


def integer(
    message: str, minimum: int, maximum: int, current: int | None, clamp: bool
) -> int | None:
    quit_key = _quit_key()
    while True:
        inp = color.color_input(message)
        if not inp.strip() and current is not None:
            return current
        if inp == quit_key:
            return None
        try:
            value = int(inp)
        except ValueError:
            _invalid(minimum, maximum)
            continue
        if minimum <= value <= maximum:
            return value
        if clamp:
            return min(max(minimum, value), maximum)
        _invalid(minimum, maximum)
