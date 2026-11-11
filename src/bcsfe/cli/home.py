"""The home menu: every feature in one filterable list, shown after a save is
loaded (interactive terminals only; the classic numbered menu in
:class:`~bcsfe.cli.feature_handler.FeatureHandler` is unchanged).

- The title summarises the save in memory (region, game version, inquiry
  code, cats, cat food, XP, NP, tickets); it is rebuilt after every feature.
- Each category of ``FeatureHandler.get_features()`` is a heading, in menu
  order; nested categories are flattened into their top-level category.
- While the query is empty, a "frequently used" section comes first (see
  :mod:`bcsfe.cli.home_usage`); with a query every feature is listed once.
- Enter runs the feature like the classic menu does; Esc / Ctrl-C start the
  exit flow, and cancelling it comes back here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from bcsfe import core
from bcsfe.cli import home_text, home_usage, main, tui
from bcsfe.cli.tui import matching

if TYPE_CHECKING:
    from bcsfe.cli.feature_handler import FeatureHandler

# ``{name}`` placeholders of a locale value (not ``\{``, not ``{{key}}``)
_PLACEHOLDER = re.compile(r"(?<!\\)\{[^{}]*\}")
# Not counted as usage (besides exit): reading it once must not make it the
# first "frequently used" row.
NOT_COUNTED = frozenset({"home_about"})


@dataclass(frozen=True)
class Feature:
    """One leaf of the feature tree."""

    path: tuple[str, ...]  # dict keys from the root (FeatureHandler.get_feature)
    name: str  # locale key of the name: identity for usage and de-duplication
    label: str  # the name as the classic menu shows it (first alias), markup
    hint: str  # category names, "" at the top level
    match_label: str  # label for matching: without values filled into it
    keywords: str  # hidden text that also matches
    exits: bool = False  # the exit feature

    def choice(self) -> tui.Choice:
        return tui.Choice(self.label, self.keywords, self.hint)


@dataclass(frozen=True)
class Category:
    label: str  # heading; "" for features at the top level (exit)
    features: tuple[Feature, ...]


@dataclass(frozen=True)
class Menu:
    choices: list[tui.Choice]
    targets: list[Feature | None]  # per choice; None for headings
    filter: Callable[[str], list[int]]


# -- names and keywords ----------------------------------------------------------


def _tidy(text: str) -> str:
    """Plain text: no colour markup, no ``{placeholders}``, single spaces."""
    text = _PLACEHOLDER.sub(" ", matching.strip_markup(text))
    return " ".join(text.split())


def _localized(key: str) -> str:
    name = core.localize(key)
    if name == key:  # e.g. a home_ key missing from an old data folder
        name = home_text.tr(key)
    return name


def display_name(key: str) -> str:
    """The name of a feature or category as the classic menu shows it."""
    return core.core_data.local_manager.get_all_aliases(_localized(key))[0]


def _aliases(text: str) -> list[str]:
    return core.core_data.local_manager.get_all_aliases(text)


def _english(key: str) -> list[str]:
    value = core.core_data.local_manager.en_properties.get(key)
    if value is None:
        return []
    return [_tidy(alias) for alias in _aliases(value[0])]


_template_cache: dict[tuple[int, str], str | None] = {}


def template_key(text: str) -> str | None:
    """Locale key whose value produced ``text`` with its placeholders filled
    in (``get_features`` localises some names itself, e.g.
    ``save_save_documents`` with the save path), or None."""
    manager = core.core_data.local_manager
    cached = (id(manager), text)
    if cached in _template_cache:
        return _template_cache[cached]
    best: tuple[int, str] | None = None
    for properties in (manager.all_properties, manager.en_properties):
        for key, (template, _) in properties.items():
            if "{" not in template or "{{" in template:
                continue
            parts = _PLACEHOLDER.split(template)
            literal = sum(len(part.strip()) for part in parts)
            if len(parts) < 2 or literal < 4 or (best and literal <= best[0]):
                continue
            pattern = ".*".join(re.escape(part) for part in parts)
            if re.fullmatch(pattern, text, re.DOTALL):
                best = (literal, key)
        if best is not None:
            break
    result = None if best is None else best[1]
    _template_cache[cached] = result
    return result


def _is_locale_key(key: str) -> bool:
    manager = core.core_data.local_manager
    return (
        key in manager.all_properties
        or key in manager.en_properties
        or key in home_text.FALLBACK
    )


def _unique(words: Iterable[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        norm = matching.normalize(word).strip()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(word.strip())
    return " ".join(out)


def make_feature(
    path: tuple[str, ...], categories: tuple[str, ...], exits: bool = False
) -> Feature:
    """``path`` ends with the dict key of the feature, ``categories`` are the
    dict keys of the categories above it."""
    key = path[-1]
    label = display_name(key)
    if _is_locale_key(key):
        name = key
        match_label = label
        words = [*_aliases(_localized(key)), *_english(key), key]
    else:  # already localised (the key text contains e.g. a path)
        template = template_key(key)
        if template is None:  # unknown: match it as it is, like upstream
            name = key
            match_label = label
            words = [*_aliases(key)]
        else:  # match the words of the name, not the filled-in values
            name = template
            match_label = _tidy(_aliases(_localized(template))[0])
            words = [_tidy(a) for a in _aliases(_localized(template))]
            words += [*_english(template), template]
    for category in categories:
        words += [*_aliases(_localized(category)), *_english(category)]
    hint = " / ".join(display_name(category) for category in categories)
    return Feature(path, name, label, hint, match_label, _unique(words), exits)


def _leaves(
    tree: dict[str, Any], path: tuple[str, ...], categories: tuple[str, ...]
) -> Iterator[Feature]:
    for key, value in tree.items():
        if isinstance(value, dict):
            sub = cast("dict[str, Any]", value)
            yield from _leaves(sub, path + (key,), categories + (key,))
        elif callable(value):
            yield make_feature(path + (key,), categories)


def collect(features: dict[str, Any]) -> list[Category]:
    """The categories of the feature tree in menu order.  Consecutive
    features at the top level form a category without a heading."""
    categories: list[Category] = []
    loose: list[Feature] = []

    def flush() -> None:
        if loose:
            categories.append(Category("", tuple(loose)))
            loose.clear()

    for key, value in features.items():
        if isinstance(value, dict):
            flush()
            tree = cast("dict[str, Any]", value)
            leaves = tuple(_leaves(tree, (key,), (key,)))
            categories.append(Category(display_name(key), leaves))
        elif callable(value):
            exits = value is main.Main.exit_editor
            loose.append(make_feature((key,), (), exits))
    flush()
    return categories


# -- menu ------------------------------------------------------------------------


def heading(label: str) -> tui.Choice:
    return tui.Choice(home_text.tr("home_heading", name=label), heading=True)


def build_menu(categories: Sequence[Category], favourites: Sequence[str]) -> Menu:
    """Rows: the ``favourites`` (feature names) under their heading, then
    each category; a blank separator before a category without a heading.

    The custom filter lists every row while the query is empty; otherwise
    only the first row of each feature in its category (the canonical rows),
    so that a favourite is not listed twice."""
    choices: list[tui.Choice] = []
    targets: list[Feature | None] = []
    first: dict[str, Feature] = {}
    for category in categories:
        for feature in category.features:
            first.setdefault(feature.name, feature)

    picked = [first[name] for name in favourites if name in first]
    if picked:
        choices.append(heading(home_text.tr("home_favourites")))
        targets.append(None)
        for feature in picked:
            choices.append(feature.choice())
            targets.append(feature)

    canonical: dict[str, int] = {}
    for category in categories:
        if category.label:
            choices.append(heading(category.label))
            targets.append(None)
        elif choices:
            choices.append(tui.Choice("", heading=True))
            targets.append(None)
        for feature in category.features:
            canonical.setdefault(feature.name, len(choices))
            choices.append(feature.choice())
            targets.append(feature)

    rows = list(canonical.values())
    matched = [cast(Feature, targets[i]) for i in rows]
    matcher = matching.Matcher(
        [f.match_label for f in matched], [f.keywords for f in matched]
    )

    def filter(query: str) -> list[int]:
        if not matching.split_query(query):
            return list(range(len(choices)))
        return [rows[i] for i in matcher.filter(query)]

    return Menu(choices, targets, filter)


def _number(value: int) -> str:
    return f"{value:,}"


def title(save_file: core.SaveFile) -> str:
    """Summary of the save in memory, then the prompt line (the line the list
    leaves in the scrollback)."""
    cats = save_file.cats.cats
    unlocked = len(save_file.cats.get_unlocked_cats())
    lines = [
        home_text.tr(
            "home_summary_save",
            cc=save_file.cc.get_code(),
            gv=save_file.game_version.to_string(),
            inquiry_code=main.Main.masked_inquiry_code(save_file),
            cats=_number(unlocked),
            cats_total=_number(len(cats)),
        ),
        home_text.tr(
            "home_summary_items",
            catfood=_number(save_file.catfood),
            xp=_number(save_file.xp),
            np=_number(save_file.np),
            rare_tickets=_number(save_file.rare_tickets),
            platinum_tickets=_number(save_file.platinum_tickets),
            legend_tickets=_number(save_file.legend_tickets),
        ),
        home_text.tr("home_prompt"),
    ]
    return "\n".join(lines)


# -- loop ------------------------------------------------------------------------


def counted(feature: Feature) -> bool:
    """Whether using ``feature`` counts for "frequently used"."""
    return not feature.exits and feature.name not in NOT_COUNTED


class Home:
    def __init__(
        self, handler: FeatureHandler, usage: home_usage.Usage | None = None
    ) -> None:
        self.handler = handler
        self.usage = usage if usage is not None else home_usage.Usage.load()

    def menu(self) -> Menu:
        categories = collect(self.handler.get_features())
        names = {f.name for c in categories for f in c.features if counted(f)}
        return build_menu(categories, self.usage.favourites(names))

    def run(self) -> None:
        """Show the home until the editor exits.  Returns when the prompts are
        no longer interactive (``ui_mode`` switched to classic, or the list
        cannot start), so that the classic menu takes over."""
        while tui.is_interactive():
            menu = self.menu()
            print()
            try:
                result = tui.pick(
                    title(self.handler.save_file),
                    menu.choices,
                    filter=menu.filter,
                    hint=home_text.tr("home_hint"),
                    fallback=False,
                )
            except tui.TuiUnavailable:
                return
            feature = None
            if result is not None and result.indexes:
                feature = menu.targets[result.indexes[0]]
            if feature is None or feature.exits:  # Esc / Ctrl-C or the exit row
                self.exit()
                continue
            if counted(feature):
                self.usage.record(feature.name)
            self.handler.run_feature(list(feature.path))

    def exit(self) -> None:
        """The exit flow; returns when it is cancelled."""
        self.handler.do_save_actions()
        main.Main.exit_editor(self.handler.save_file, cancellable=True)
