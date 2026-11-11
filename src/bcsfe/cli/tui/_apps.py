"""prompt_toolkit implementation of the widgets.

Imported lazily (see :mod:`bcsfe.cli.tui.widgets`) so that the classic path
never imports prompt_toolkit.  Every widget renders inline below the cursor,
erases itself when done and prints one summary line instead.
"""

from __future__ import annotations

import codecs
import math
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import (
    ANSI,
    FormattedText,
    StyleAndTextTuples,
    to_formatted_text,
)
from prompt_toolkit.formatted_text.utils import (
    fragment_list_to_text,
    fragment_list_width,
    split_lines,
)
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    ConditionalContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.containers import AnyContainer
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.output import Output
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from bcsfe import core
from bcsfe.cli import color
from bcsfe.cli.tui import matching, strings
from bcsfe.cli.tui.base import Choice, ExtraKey, ListFilter, ListResult

R = TypeVar("R")
Handler = Callable[[KeyPressEvent], None]

MAX_ROWS = 15
ESC_TIMEOUT = 0.05  # seconds to wait after ESC before treating it as the Esc key
KEY_TIMEOUT = 0.4
CHECK_ON = "[x] "
CHECK_OFF = "[ ] "


@dataclass(frozen=True)
class Glyphs:
    pointer: str
    done: str
    cancel: str
    error: str
    ellipsis: str


# Only East Asian Width "N" symbols: one cell even in "ambiguous = wide" mode.
UNICODE_GLYPHS = Glyphs("❯ ", "✔ ", "✘ ", "✘ ", "…")
ASCII_GLYPHS = Glyphs("> ", "* ", "x ", "! ", "...")

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


# -- styling ---------------------------------------------------------------


def _theme(name: str, default: str) -> str:
    try:
        value = core.core_data.theme_manager.get_theme_color(name)
    except Exception:  # theme not loaded
        return default
    return value if _HEX.match(value) else default


def build_style() -> Style:
    accent = _theme("tertiary", "#00ffff")
    success = _theme("success", "#00ff00")
    error = _theme("error", "#ff0000")
    dim = "#8a8a8a"
    return Style(
        [
            ("prompt", f"{accent} bold"),
            ("pointer", f"{accent} bold"),
            ("current", "bg:#3a3a3a"),
            ("check-on", f"{success} bold"),
            ("check-off", dim),
            ("counter", dim),
            ("selected-count", f"{success} bold"),
            ("row-hint", dim),
            ("heading", f"{accent} bold"),
            ("no-match", f"{dim} italic"),
            ("hint", dim),
            ("info", dim),
            ("error", f"{error} bold"),
            ("button", ""),
            ("button-current", f"bg:{accent} #000000 bold"),
            ("done-ok", f"{success} bold"),
            ("done-cancel", dim),
            ("answer", accent),
        ]
    )


def markup(text: str) -> StyleAndTextTuples:
    """Editor colour markup (``<@t>..</>``) -> prompt_toolkit fragments."""
    try:
        ansi = color.colorize(text)
    except Exception:  # theme not loaded
        return [("", matching.strip_markup(text))]
    return to_formatted_text(ANSI(ansi))


def plain(text: str) -> str:
    return matching.strip_markup(text)


def width_of(fragments: StyleAndTextTuples) -> int:
    return fragment_list_width(fragments)


def truncate(
    fragments: StyleAndTextTuples, width: int, ellipsis: str = "…"
) -> StyleAndTextTuples:
    """Cut ``fragments`` to ``width`` cells (wide characters count 2)."""
    if width <= 0:
        return []
    if fragment_list_width(fragments) <= width:
        return fragments
    limit = width - get_cwidth(ellipsis)
    out: StyleAndTextTuples = []
    used = 0
    for item in fragments:
        style, text = item[0], item[1]
        chars: list[str] = []
        for char in text:
            w = get_cwidth(char)
            if used + w > limit:
                if chars:
                    out.append((style, "".join(chars)))
                out.append((style, ellipsis))
                return out
            chars.append(char)
            used += w
        if chars:
            out.append((style, "".join(chars)))
    return out


def _join_lines(lines: Sequence[StyleAndTextTuples]) -> StyleAndTextTuples:
    out: StyleAndTextTuples = []
    for i, line in enumerate(lines):
        if i:
            out.append(("", "\n"))
        out.extend(line)
    return out


def _lines(fragments: StyleAndTextTuples) -> list[StyleAndTextTuples]:
    lines = list(split_lines(fragments))
    while lines and not fragment_list_to_text(lines[-1]).strip():
        lines.pop()
    while lines and not fragment_list_to_text(lines[0]).strip():
        lines.pop(0)
    return lines


def last_line(fragments: StyleAndTextTuples) -> StyleAndTextTuples:
    lines = _lines(fragments)
    return lines[-1] if lines else []


def _output() -> Output:
    return get_app_session().output


def glyphs() -> Glyphs:
    """Symbols for the current output; ASCII if it cannot encode Unicode."""
    try:
        encoding = codecs.lookup(_output().encoding() or "ascii").name
    except Exception:
        return ASCII_GLYPHS
    return UNICODE_GLYPHS if encoding.startswith("utf") else ASCII_GLYPHS


def _print(fragments: StyleAndTextTuples) -> None:
    print_formatted_text(
        FormattedText(fragments),
        style=build_style(),
        output=_output(),
        include_default_pygments_style=False,
    )


def fit_title(title: StyleAndTextTuples, reserve: int) -> StyleAndTextTuples:
    """Return the part of ``title`` shown inside the widget.

    If the title is too tall for the terminal (``reserve`` rows are needed for
    the rest of the widget), all but its last line are printed normally
    instead, so the whole text stays readable in the scrollback."""
    lines = _lines(title)
    try:
        size = _output().get_size()
    except Exception:
        return _join_lines(lines)
    columns = max(1, size.columns)
    height = sum(max(1, math.ceil(width_of(line) / columns)) for line in lines)
    if len(lines) <= 1 or height <= max(1, size.rows - reserve):
        return _join_lines(lines)
    _print(_join_lines(lines[:-1]))
    return lines[-1]


def print_summary(title: StyleAndTextTuples, answer: str | None) -> None:
    """Print the one-line record left behind by a finished widget: the last
    title line and the answer (None = cancelled)."""
    try:
        columns = _output().get_size().columns
    except Exception:
        columns = 80
    g = glyphs()
    if answer is None:
        mark: StyleAndTextTuples = [("class:done-cancel", g.cancel)]
        answer_frags: StyleAndTextTuples = [
            ("class:done-cancel", plain(strings.tr("tui_cancelled")))
        ]
    else:
        mark = [("class:done-ok", g.done)]
        answer_frags = [("class:answer", answer.replace("\n", " "))]
    budget = max(20, columns - 1) - width_of(mark)
    answer_width = min(width_of(answer_frags), max(8, budget // 2))
    head = truncate(last_line(title), budget - answer_width - 1, g.ellipsis)
    space: StyleAndTextTuples = [("", " ")] if head else []
    answer_frags = truncate(
        answer_frags, budget - width_of(head) - len(space), g.ellipsis
    )
    _print(mark + head + space + answer_frags)


# -- application plumbing ------------------------------------------------------


def guarded(func: Handler) -> Handler:
    """Turn exceptions in a key handler into an exception of ``app.run()``."""

    def handler(event: KeyPressEvent) -> None:
        try:
            func(event)
        except Exception as e:
            if not event.app.is_done:
                event.app.exit(exception=e)

    return handler


def bind(
    kb: KeyBindings, *keys: Keys | str, eager: bool = True
) -> Callable[[Handler], Handler]:
    def register(func: Handler) -> Handler:
        handler = guarded(func)
        for key in keys:
            kb.add(key, eager=eager)(handler)
        return func

    return register


def base_bindings(buffer: Buffer | None = None) -> KeyBindings:
    kb = KeyBindings()

    @bind(kb, "escape", "c-c", Keys.SIGINT)
    def _cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    if buffer is not None:
        query = buffer

        @bind(kb, Keys.BracketedPaste)
        def _paste(event: KeyPressEvent) -> None:
            text = event.data.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
            query.insert_text(text)

    return kb


def make_app(root: AnyContainer, focus: Window, kb: KeyBindings) -> Application[Any]:
    app: Application[Any] = Application(
        layout=Layout(root, focused_element=focus),
        key_bindings=kb,
        style=build_style(),
        include_default_pygments_style=False,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )
    app.ttimeoutlen = ESC_TIMEOUT
    app.timeoutlen = KEY_TIMEOUT
    return app


def fresh_line(output: Output) -> None:
    """Make sure the widget starts at column 0.

    Inline rendering starts at the cursor, so text left on the current line
    (output without a newline, keys typed ahead of a classic prompt) would be
    drawn over.  Writing a full line of spaces and CR keeps the cursor on the
    line when it is at column 0 (the terminal defers the wrap) and moves it to
    the next line otherwise."""
    try:
        columns = output.get_size().columns
    except Exception:
        return
    output.write_raw(" " * columns + "\r")
    output.flush()


def run(app: Application[R]) -> R:
    try:
        sys.stdout.flush()  # classic output printed with end="" comes first
    except Exception:
        pass
    fresh_line(app.output)
    return app.run()


def hint_windows(texts: Iterable[str]) -> list[AnyContainer]:
    return [
        Window(
            FormattedTextControl([("class:hint", "  " + plain(text))]),
            wrap_lines=True,
            dont_extend_height=True,
        )
        for text in texts
    ]


def title_window(title: StyleAndTextTuples) -> Window:
    return Window(
        FormattedTextControl(title),
        wrap_lines=True,
        dont_extend_height=True,
        height=Dimension(weight=10),  # grow before the list does
    )


def prompt_window() -> Window:
    return Window(
        FormattedTextControl([("class:prompt", "> ")]),
        width=2,
        height=1,
        dont_extend_width=True,
    )


# -- select / multi_select -------------------------------------------------------


class ListState:
    """Filter, cursor, scroll and check marks of a list widget.

    ``visible`` holds indexes into ``choices`` in display order; ``cursor`` is
    a position in ``visible``.  Heading rows (``Choice.heading``) can be
    visible but are skipped by the cursor and never checked or returned."""

    def __init__(
        self,
        choices: Sequence[Choice],
        *,
        multi: bool,
        default: int = 0,
        selected: Iterable[int] = (),
        max_rows: int = MAX_ROWS,
        filter: ListFilter | None = None,
        query: str = "",
    ) -> None:
        total = len(choices)
        self.choices = list(choices)
        self.multi = multi
        self.selectable = [not c.heading for c in self.choices]
        self.matcher = matching.Matcher(
            [c.label for c in self.choices], [c.keywords for c in self.choices]
        )
        self.custom_filter = filter
        self.visible: list[int] = []
        self.cursor = 0
        self.top = 0
        self.selected: set[int] = {
            i for i in selected if 0 <= i < total and self.selectable[i]
        }
        self.rows = max(1, min(total, max_rows))
        self.page = self.rows
        self._labels: dict[int, StyleAndTextTuples] = {}
        self.glyphs = glyphs()
        self.refilter(query)
        default = min(max(default, 0), max(total - 1, 0))
        if total and self.selectable[default] and default in self.visible:
            self.cursor = self.visible.index(default)

    # state changes
    def _filtered(self, query: str) -> list[int]:
        total = len(self.choices)
        if self.custom_filter is not None:
            seen: set[int] = set()
            rows: list[int] = []
            for i in self.custom_filter(query):
                if 0 <= i < total and i not in seen:
                    seen.add(i)
                    rows.append(i)
            return rows
        if not matching.split_query(query):
            return list(range(total))
        return [i for i in self.matcher.filter(query) if self.selectable[i]]

    def refilter(self, query: str) -> None:
        self.visible = self._filtered(query)
        self.top = 0
        self.cursor = self._snap(0, 1)

    def _snap(self, position: int, direction: int) -> int:
        """Nearest selectable position from ``position``, searching in
        ``direction`` first; ``position`` itself when nothing is selectable."""
        count = len(self.visible)
        if count == 0:
            return 0
        position = max(0, min(count - 1, position))
        for step in (direction, -direction):
            p = position
            while 0 <= p < count:
                if self.selectable[self.visible[p]]:
                    return p
                p += step
        return position

    def current(self) -> int | None:
        if not self.visible:
            return None
        index = self.visible[self.cursor]
        return index if self.selectable[index] else None

    def move(self, delta: int) -> None:
        if self.visible:
            self.cursor = self._snap(self.cursor + delta, 1 if delta >= 0 else -1)

    def move_to(self, position: int, direction: int = 1) -> None:
        if self.visible:
            self.cursor = self._snap(position, direction)

    def toggle(self) -> None:
        index = self.current()
        if index is None:
            return
        if index in self.selected:
            self.selected.remove(index)
        else:
            self.selected.add(index)

    def check_visible(self) -> None:
        self.selected.update(i for i in self.visible if self.selectable[i])

    def clear(self) -> None:
        self.selected.clear()

    def multi_result(self) -> list[int] | None:
        """Checked rows, or the highlighted row when nothing is checked."""
        if self.selected:
            return sorted(self.selected)
        index = self.current()
        return None if index is None else [index]

    # rendering
    def label(self, index: int) -> StyleAndTextTuples:
        cached = self._labels.get(index)
        if cached is None:
            cached = markup(self.choices[index].label.replace("\n", " "))
            self._labels[index] = cached
        return cached

    def scroll(self, height: int) -> None:
        height = max(1, height)
        self.page = height
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + height:
            self.top = self.cursor - height + 1
        # keep the heading right above the highlighted row in view
        above = self.cursor - 1
        if (
            0 <= above < self.top
            and not self.selectable[self.visible[above]]
            and self.cursor - above < height
        ):
            self.top = above
        self.top = max(0, min(self.top, max(0, len(self.visible) - height)))

    def row(self, position: int, width: int) -> StyleAndTextTuples:
        index = self.visible[position]
        choice = self.choices[index]
        pointer_space = " " * get_cwidth(self.glyphs.pointer)
        if choice.heading:
            frags: StyleAndTextTuples = [("", pointer_space)]
            heading: StyleAndTextTuples = [
                ("class:heading " + f[0], f[1]) for f in self.label(index)
            ]
            room = width - len(pointer_space)
            return frags + truncate(heading, room, self.glyphs.ellipsis)
        is_current = position == self.cursor
        frags = [
            ("class:pointer", self.glyphs.pointer)
            if is_current
            else ("", pointer_space)
        ]
        if self.multi:
            if index in self.selected:
                frags.append(("class:check-on", CHECK_ON))
            else:
                frags.append(("class:check-off", CHECK_OFF))
        room = width - width_of(frags)
        hint: StyleAndTextTuples = []
        if choice.hint:
            hint = [("class:row-hint", plain(choice.hint).replace("\n", " "))]
        if hint and width_of(hint) + 2 < room:
            label = truncate(
                self.label(index), room - width_of(hint) - 2, self.glyphs.ellipsis
            )
            pad = room - width_of(label) - width_of(hint)
            frags += label + [("", " " * pad)] + hint
        else:
            frags += truncate(self.label(index), room, self.glyphs.ellipsis)
        if is_current:
            fill = width - width_of(frags)
            if fill > 0:
                frags.append(("", " " * fill))
            frags = [("class:current " + item[0], item[1]) for item in frags]
        return frags

    def counter(self) -> StyleAndTextTuples:
        frags: StyleAndTextTuples = [("", " ")]
        if self.multi:
            style = "class:selected-count" if self.selected else "class:counter"
            count = strings.tr("tui_selected_count", count=len(self.selected))
            frags += [(style, plain(count)), ("", "  ")]
        shown = sum(1 for i in self.visible if self.selectable[i])
        total = sum(1 for flag in self.selectable if flag)
        frags.append(
            (
                "class:counter",
                plain(strings.tr("tui_counter", shown=shown, total=total)),
            )
        )
        return frags


class ListControl(UIControl):
    """Renders only the visible rows of a :class:`ListState`."""

    def __init__(self, state: ListState) -> None:
        self.state = state

    def create_content(self, width: int, height: int) -> UIContent:
        state = self.state
        state.scroll(height)
        if not state.visible:
            text = "  " + plain(strings.tr("tui_no_match"))
            lines: list[StyleAndTextTuples] = [[("class:no-match", text)]]
        else:
            end = min(len(state.visible), state.top + max(1, height))
            lines = [state.row(p, width) for p in range(state.top, end)]
        return UIContent(
            get_line=lambda i: lines[i], line_count=len(lines), show_cursor=False
        )


def _list_summary(state: ListState, result: Sequence[int] | None) -> str | None:
    if result is None:
        return None
    labels = [plain(state.choices[i].label).strip() for i in result]
    if len(labels) <= 3:
        return " / ".join(labels)
    many = strings.tr(
        "tui_summary_many", count=len(labels), items=" / ".join(labels[:3])
    )
    return plain(many)


_KEY_NAMES = {"escape": "Esc", "tab": "Tab", "s-tab": "Shift-Tab", "enter": "Enter"}


def key_label(key: str) -> str:
    """Display name of a prompt_toolkit key: ``c-o`` -> ``Ctrl-O``, ``f2`` -> ``F2``."""
    if key in _KEY_NAMES:
        return _KEY_NAMES[key]
    if key.startswith("c-") and len(key) > 2:
        return "Ctrl-" + key[2:].upper()
    if re.fullmatch(r"f\d{1,2}", key):
        return key.upper()
    return key


def run_list(
    title: str,
    choices: Sequence[Choice],
    *,
    multi: bool,
    default: int = 0,
    selected: Iterable[int] = (),
    footer: str | None = None,
    filter: ListFilter | None = None,
    extra_keys: Sequence[ExtraKey] = (),
    query: str = "",
) -> ListResult | None:
    state = ListState(
        choices,
        multi=multi,
        default=default,
        selected=selected,
        filter=filter,
        query=query,
    )
    hints = [strings.tr("tui_hint_select")]
    if multi:
        hints.append(strings.tr("tui_hint_multi"))
    if extra_keys:
        hints.append("  ".join(f"{key_label(k.key)} {k.label}" for k in extra_keys))
    footer_frags = markup(footer) if footer else []
    reserve = 2 + min(state.rows, 3) + len(hints) + len(_lines(footer_frags))
    title_frags = fit_title(markup(title), reserve)

    buffer = Buffer(
        document=Document(query, len(query)),
        multiline=False,
        on_text_changed=lambda buf: state.refilter(buf.text),
    )
    query_window = Window(BufferControl(buffer=buffer), height=1)
    body: list[AnyContainer] = []
    if title_frags:
        body.append(title_window(title_frags))
    body.append(
        VSplit(
            [
                prompt_window(),
                query_window,
                Window(
                    FormattedTextControl(state.counter),
                    height=1,
                    dont_extend_width=True,
                ),
            ],
            height=1,
        )
    )
    body.append(
        Window(
            ListControl(state),
            height=Dimension(min=1, max=state.rows, preferred=state.rows),
        )
    )
    if footer_frags:
        body.append(
            Window(
                FormattedTextControl(footer_frags),
                wrap_lines=True,
                dont_extend_height=True,
            )
        )
    body += hint_windows(hints)

    kb = base_bindings(buffer)

    @bind(kb, "up", "c-p")
    def _up(event: KeyPressEvent) -> None:
        state.move(-1)

    @bind(kb, "down", "c-n")
    def _down(event: KeyPressEvent) -> None:
        state.move(1)

    @bind(kb, "pageup")
    def _page_up(event: KeyPressEvent) -> None:
        state.move(-state.page)

    @bind(kb, "pagedown")
    def _page_down(event: KeyPressEvent) -> None:
        state.move(state.page)

    @bind(kb, "home")
    def _home(event: KeyPressEvent) -> None:
        state.move_to(0, 1)

    @bind(kb, "end")
    def _end(event: KeyPressEvent) -> None:
        state.move_to(len(state.visible) - 1, -1)

    def chosen() -> list[int] | None:
        if multi:
            return state.multi_result()
        index = state.current()
        return None if index is None else [index]

    @bind(kb, "enter")
    def _enter(event: KeyPressEvent) -> None:
        rows = chosen()
        if rows is not None:
            event.app.exit(result=ListResult(tuple(rows), None, buffer.text))

    for extra in extra_keys:

        def _extra(event: KeyPressEvent, extra: ExtraKey = extra) -> None:
            rows = chosen() or []
            event.app.exit(result=ListResult(tuple(rows), extra.name, buffer.text))

        bind(kb, extra.key)(_extra)

    if multi:

        @bind(kb, "tab")
        def _toggle_down(event: KeyPressEvent) -> None:
            state.toggle()
            state.move(1)

        @bind(kb, "s-tab")
        def _toggle_up(event: KeyPressEvent) -> None:
            state.toggle()
            state.move(-1)

        @bind(kb, "c-a")
        def _check_all(event: KeyPressEvent) -> None:
            state.check_visible()

        @bind(kb, "c-d")
        def _clear(event: KeyPressEvent) -> None:
            state.clear()

    app: Application[ListResult | None] = make_app(HSplit(body), query_window, kb)
    result = run(app)
    if result is None:
        print_summary(title_frags, None)
    elif result.key is None:
        print_summary(title_frags, _list_summary(state, result.indexes))
    return result


def pick(
    title: str,
    choices: Sequence[Choice],
    *,
    multi: bool,
    default: int,
    selected: Iterable[int],
    footer: str | None,
    filter: ListFilter | None,
    extra_keys: Sequence[ExtraKey],
    query: str,
) -> ListResult | None:
    return run_list(
        title,
        choices,
        multi=multi,
        default=default,
        selected=selected,
        footer=footer,
        filter=filter,
        extra_keys=extra_keys,
        query=query,
    )


def select(
    title: str, choices: Sequence[Choice], *, default: int, footer: str | None
) -> int | None:
    result = run_list(title, choices, multi=False, default=default, footer=footer)
    return result.indexes[0] if result is not None and result.indexes else None


def multi_select(
    title: str,
    choices: Sequence[Choice],
    *,
    selected: Iterable[int],
    footer: str | None,
) -> list[int] | None:
    result = run_list(title, choices, multi=True, selected=selected, footer=footer)
    return list(result.indexes) if result is not None else None


# -- confirm ---------------------------------------------------------------------


def confirm(message: str, *, default: bool) -> bool | None:
    yes = plain(strings.tr("tui_yes"))
    no = plain(strings.tr("tui_no"))
    hints = [strings.tr("tui_hint_confirm")]
    title_frags = fit_title(markup(message), 2 + len(hints))
    answer = [default]
    yes_keys = {"y", matching.normalize(strings.localize_or("yes_key", "y")).strip()}
    no_keys = {"n"}
    quit_keys = {matching.normalize(strings.localize_or("quit_key", "q")).strip()}

    def buttons() -> StyleAndTextTuples:
        frags: StyleAndTextTuples = [("", "  ")]
        for value, label in ((True, yes), (False, no)):
            if answer[0] == value:
                frags.append(("class:button-current", f"[ {label} ]"))
            else:
                frags.append(("class:button", f"  {label}  "))
            frags.append(("", "  "))
        return frags

    button_window = Window(
        FormattedTextControl(buttons, focusable=True, show_cursor=False), height=1
    )
    body: list[AnyContainer] = []
    if title_frags:
        body.append(title_window(title_frags))
    body.append(button_window)
    body += hint_windows(hints)

    kb = base_bindings()

    @bind(kb, "left", "right", "tab", "s-tab", "h", "l")
    def _switch(event: KeyPressEvent) -> None:
        answer[0] = not answer[0]

    @bind(kb, "enter")
    def _accept(event: KeyPressEvent) -> None:
        event.app.exit(result=answer[0])

    # Not eager: specific bindings (CPR responses, ...) must keep priority.
    @bind(kb, Keys.Any, eager=False)
    def _key(event: KeyPressEvent) -> None:
        char = unicodedata.normalize("NFKC", event.data).casefold().strip()
        if char in yes_keys:
            event.app.exit(result=True)
        elif char in no_keys:
            event.app.exit(result=False)
        elif char in quit_keys:  # the classic prompt's quit key also cancels
            event.app.exit(result=None)

    app: Application[bool | None] = make_app(HSplit(body), button_window, kb)
    result = run(app)
    print_summary(title_frags, None if result is None else (yes if result else no))
    return result


# -- text / integer --------------------------------------------------------------


@dataclass(frozen=True)
class Accepted(Generic[R]):
    value: R
    shown: str


def run_entry(
    message: str,
    *,
    default: str,
    info: StyleAndTextTuples,
    hints: Sequence[str],
    check: Callable[[str], Accepted[R] | str | None],
) -> Accepted[R] | None:
    """A one-line text field.  On Enter ``check`` returns the result, an error
    text (shown under the field, input kept) or None to cancel."""
    title_frags = fit_title(markup(message), 3 + len(hints))
    error: list[StyleAndTextTuples] = [[]]

    def clear_error(_: Buffer) -> None:
        error[0] = []

    buffer = Buffer(
        multiline=False,
        document=Document(default, len(default)),
        on_text_changed=clear_error,
    )
    input_window = Window(BufferControl(buffer=buffer), height=1)
    row: list[AnyContainer] = [prompt_window(), input_window]
    if info:
        row.append(
            Window(
                FormattedTextControl([("", "  ")] + info),
                height=1,
                dont_extend_width=True,
            )
        )
    body: list[AnyContainer] = []
    if title_frags:
        body.append(title_window(title_frags))
    body.append(VSplit(row, height=1))
    body.append(
        ConditionalContainer(
            Window(
                FormattedTextControl(lambda: error[0]),
                wrap_lines=True,
                dont_extend_height=True,
            ),
            filter=Condition(lambda: bool(error[0])),
        )
    )
    body += hint_windows(hints)

    kb = base_bindings(buffer)

    @bind(kb, "enter")
    def _submit(event: KeyPressEvent) -> None:
        outcome = check(buffer.text)
        if outcome is None or isinstance(outcome, Accepted):
            event.app.exit(result=outcome)
        else:
            error[0] = [("class:error", "  " + glyphs().error + plain(outcome))]

    app: Application[Accepted[R] | None] = make_app(HSplit(body), input_window, kb)
    result = run(app)
    print_summary(title_frags, None if result is None else result.shown)
    return result


def text(
    message: str, *, default: str, validate: Callable[[str], str | None] | None
) -> str | None:
    def check(value: str) -> Accepted[str] | str:
        problem = validate(value) if validate is not None else None
        if problem is not None:
            return problem
        return Accepted(value, value if value else plain(strings.tr("tui_empty_value")))

    result = run_entry(
        message,
        default=default,
        info=[],
        hints=[strings.tr("tui_hint_input")],
        check=check,
    )
    return None if result is None else result.value


def fmt(value: int) -> str:
    return f"{value:,}"


def parse_int(text: str) -> int | None:
    """Integer from user text: full-width digits, ``1,000`` and ``1_000`` ok."""
    raw = unicodedata.normalize("NFKC", text).strip()
    raw = raw.replace(",", "").replace("_", "").replace(" ", "")
    try:
        return int(raw)
    except ValueError:
        return None


def integer(
    message: str,
    *,
    minimum: int,
    maximum: int,
    current: int | None,
    clamp: bool,
    show_range: bool = True,
    current_label: str | None = None,
) -> int | None:
    info: StyleAndTextTuples = []
    if show_range:
        info.append(
            (
                "class:info",
                plain(strings.tr("tui_range", min=fmt(minimum), max=fmt(maximum))),
            )
        )
    hint = strings.tr("tui_hint_input")
    if current is not None:
        label = plain(current_label or strings.tr("tui_current_label"))
        if info:
            info.append(("", "  "))
        info.append(("class:info", f"{label} {fmt(current)}"))
        hint += "  " + strings.tr("tui_hint_keep", value=fmt(current))

    quit_key = strings.localize_or("quit_key", "q")

    def check(value: str) -> Accepted[int] | str | None:
        if not unicodedata.normalize("NFKC", value).strip():
            if current is not None:
                return Accepted(current, str(current))
            return strings.tr("tui_error_empty")
        if value.strip() == quit_key:  # the classic prompt's quit key cancels
            return None
        number = parse_int(value)
        if number is None:
            return strings.tr("tui_error_not_number")
        if not minimum <= number <= maximum:
            if not clamp:
                return strings.tr("tui_error_range", min=fmt(minimum), max=fmt(maximum))
            number = min(max(minimum, number), maximum)
        return Accepted(number, str(number))

    result = run_entry(message, default="", info=info, hints=[hint], check=check)
    return None if result is None else result.value
