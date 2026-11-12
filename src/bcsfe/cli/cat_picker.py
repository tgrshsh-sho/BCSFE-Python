"""Cat picker: every cat of the save in one filterable, checkable list.

In interactive mode :class:`~bcsfe.cli.edits.cat_editor.CatEditor` opens this
picker instead of upstream's selection-method menu, id prompt and "finished
selecting?" loop.  A row is the cat id and its first-form name, with the
rarity and the level (or "not owned") as a hint.  :class:`CatFilter` reads
the query:

- ids and id ranges (``882``, ``882-886``, ``882〜886``, full-width digits).
  A query of only ids lists exactly those cats, in the order typed;
- facets, matched exactly: owned / not owned, obtainable / unobtainable (the
  cat guide flag upstream's obtainable filter uses), gacha / non-gacha
  (``unlock_source == 2``), the rarity names of the game data, and versions
  with a dot (``16.0.0``, ``16.0``: a prefix of the version the cat was added
  in);
- anything else must be part of a form name or of its reading.

Terms of the same facet are alternatives (OR); different facets, the ids and
the name parts must all match (AND).  Ctrl-O runs upstream's selection-method
menu and adds its result to the checked cats.

What does not change while editing (names, rarity, ...) is cached per save.
The interactive level input (:func:`upgrade_input`) lives here as well.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from bcsfe import core
from bcsfe.cli import color, tui
from bcsfe.cli.tui import matching
from bcsfe.cli.tui import strings as tui_strings

MORE = "more"
MORE_KEY = "c-o"

# Facets of the query (see CatFilter)
OWNED = "owned"
OBTAINABLE = "obtainable"
GACHA = "gacha"
RARITY = "rarity"

# English texts for keys missing from an older data folder (the editor copies
# the locale files there only when its version changes).  Keep in sync with
# files/locales/en/fork/cats.properties.
FALLBACK: dict[str, str] = {
    "cats_picker_title": "Select cats:",
    "cats_picker_footer": (
        "e.g. <@t>{not_owned} {rarity}</> / <@t>{version}</> / <@t>882-886</>"
        " / part of a name"
    ),
    "cats_picker_more": "Other ways to select",
    "cats_no_name": "(no name)",
    "cats_hint_level": "Lv{base}+{plus}",
    "cats_facet_owned": "unlocked|owned",
    "cats_facet_not_owned": "locked|unowned",
    "cats_facet_obtainable": "obtainable",
    "cats_facet_unobtainable": "unobtainable",
    "cats_facet_gacha": "gacha",
    "cats_facet_non_gacha": "non-gacha",
    "cats_selected_summary": "Selected cats (<@t>{count}</>): {names}",
    "cats_selected_more": "and {count} more",
    "cats_selected_none": "No cats selected",
    "cats_upgrade_title": "Enter the level (max <@t>{max_base}+{max_plus}</>):",
    "cats_upgrade_title_many": (
        "Enter the level for <@t>{count}</> cats (max <@t>{max_base}+{max_plus}</>):"
    ),
    "cats_upgrade_hint": (
        "e.g. <@t>50+30</> / <@t>max+max</> / <@t>5-10+20-30</>"
        " (random in the range)\n"
        "<@t>50+</> keeps the plus level, <@t>50</> alone sets the plus level to 0"
    ),
    "cats_storage_select": "Select the items to remove:",
}


def tr(key: str, **kwargs: Any) -> str:
    """Localised text (editor colour markup); values of ``kwargs`` are shown
    literally.  Falls back to :data:`FALLBACK` when the key is missing."""
    try:
        value = core.core_data.local_manager.get_key(key, **kwargs)
    except Exception:  # locale not loaded
        value = key
    if value != key:
        return value
    value = FALLBACK.get(key, key)
    for name, arg in kwargs.items():
        value = value.replace(
            "{" + name + "}", core.LocalManager.escape_string(str(arg))
        )
    return value


def aliases(key: str) -> list[str]:
    """The ``|``-separated alternatives of a key, first one first."""
    return [a for a in core.LocalManager.get_all_aliases(tr(key)) if a.strip()]


def escape(text: str) -> str:
    return core.LocalManager.escape_string(text)


def plain(text: str) -> str:
    return matching.strip_markup(text)


def width(text: str) -> int:
    """Terminal cells of ``text`` (wide East Asian characters count 2)."""
    cells = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        cells += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return cells


def pad(text: str, cells: int) -> str:
    """``text`` filled with spaces to ``cells`` terminal cells."""
    return text + " " * max(0, cells - width(text))


# -- what the picker knows about each cat ----------------------------------------

# Names of cats the game data has no text for, e.g. "888_1"
_PLACEHOLDER = re.compile(r"[0-9]+_[0-9]+")
# Column of Unit_Explanation*.csv holding the reading of the form name (katakana)
_READING_COLUMN = 4


@dataclass(frozen=True)
class CatInfo:
    """The parts of a picker row that do not change while editing."""

    id: int
    name: str  # first-form name, "" when the game data has none
    search: str  # normalised form names and readings, "\0"-separated
    rarity: int = -1  # index into Cats.get_rarity_names, -1 if unknown
    obtainable: bool | None = None  # shown in the cat guide (None: no data)
    gacha: bool | None = None  # unlocked from the gacha (None: no data)
    version: tuple[int, ...] | None = None  # game version the cat was added in


def is_placeholder(name: str) -> bool:
    return not name.strip() or bool(_PLACEHOLDER.fullmatch(name.strip()))


def make_info(
    cat_id: int,
    names: Sequence[str],
    readings: Sequence[str] = (),
    rarity: int = -1,
    obtainable: bool | None = None,
    gacha: bool | None = None,
    version: tuple[int, ...] | None = None,
) -> CatInfo:
    real = [n.strip() for n in names if not is_placeholder(n)]
    first = names[0].strip() if names and not is_placeholder(names[0]) else ""
    words = real + [r.strip() for r in readings if r.strip()]
    search = "\0".join(matching.normalize(w) for w in words)
    return CatInfo(cat_id, first, search, rarity, obtainable, gacha, version)


def _read_names(
    gdg: core.GameDataGetter,
    lang: str,
    delimiter: core.Delimeter,
    cat: core.Cat,
) -> tuple[list[str], list[str]]:
    """Form names and their readings; reads the file once and fills the name
    cache of ``cat`` (same parsing as :meth:`core.Cat.get_names`)."""
    data = gdg.download("resLocal", f"Unit_Explanation{cat.id + 1}_{lang}.csv")
    if data is None:
        return [], []
    csv = core.CSV(data, delimiter, remove_empty=False)
    names = [line[0].to_str() for line in csv.lines]
    readings = [
        line[_READING_COLUMN].to_str()
        for line in csv.lines
        if len(line) > _READING_COLUMN
    ]
    if cat.names is None:
        cat.names = names
    return names, readings


def _version_parts(game_version: int) -> tuple[int, ...] | None:
    if game_version < 0:  # not in the upgrade menu
        return None
    return tuple(core.GameVersion(game_version).get_parts())


def load_infos(save_file: core.SaveFile) -> list[CatInfo]:
    """Build the static row data of every cat of ``save_file``."""
    gdg = core.core_data.get_game_data_getter(save_file)
    lang = core.core_data.get_lang(save_file)
    delimiter = core.Delimeter.from_country_code_res(save_file.cc)
    unit_buy = save_file.cats.read_unitbuy(save_file)
    picture_book = save_file.cats.read_nyanko_picture_book(save_file)
    guide = {entry.cat_id: entry for entry in picture_book.cats or []}
    infos: list[CatInfo] = []
    for cat in save_file.cats.cats:
        names, readings = _read_names(gdg, lang, delimiter, cat)
        buy = unit_buy.get_unit_buy(cat.id)
        entry = guide.get(cat.id)
        infos.append(
            make_info(
                cat.id,
                names,
                readings,
                rarity=-1 if buy is None else buy.rarity,
                obtainable=None if entry is None else entry.is_displayed_in_catguide,
                gacha=None if buy is None else buy.unlock_source == 2,
                version=None if buy is None else _version_parts(buy.game_version),
            )
        )
    return infos


_cache: tuple[tuple[object, ...], list[CatInfo]] | None = None


def cat_infos(save_file: core.SaveFile) -> list[CatInfo]:
    """:func:`load_infos`, cached for the save (and its region, version and
    number of cats, which load / convert features change in place)."""
    global _cache
    key = (
        id(save_file),
        save_file.cc.get_code(),
        save_file.game_version.game_version,
        len(save_file.cats.cats),
    )
    if _cache is None or _cache[0] != key:
        _cache = (key, load_infos(save_file))
    return _cache[1]


# -- the query ------------------------------------------------------------------

_SEPARATORS = re.compile(r"[\s,、]+")
_DASHES = "-~〜ー−‐–—―"
_ID_TERM = re.compile(rf"([0-9]+)(?:[{_DASHES}]([0-9]+))?")
_VERSION_TERM = re.compile(r"[0-9]+(?:\.[0-9]+)*\.?")


@dataclass
class CatQuery:
    """A parsed query (see :class:`CatFilter`)."""

    ids: list[int] | None = None  # row indexes in the order typed, no duplicates
    facets: dict[str, set[object]] = field(default_factory=lambda: {})
    versions: list[tuple[int, ...]] = field(default_factory=lambda: [])
    texts: list[str] = field(default_factory=lambda: [])

    def only_ids(self) -> bool:
        return (
            self.ids is not None
            and not self.facets
            and not self.versions
            and not self.texts
        )


def facet_words(rarity_names: Sequence[str]) -> dict[str, tuple[str, object]]:
    """Normalised facet word -> (facet, value), from the locale and the
    rarity names of the game data."""
    words: dict[str, tuple[str, object]] = {}
    for key, facet, value in (
        ("cats_facet_owned", OWNED, True),
        ("cats_facet_not_owned", OWNED, False),
        ("cats_facet_obtainable", OBTAINABLE, True),
        ("cats_facet_unobtainable", OBTAINABLE, False),
        ("cats_facet_gacha", GACHA, True),
        ("cats_facet_non_gacha", GACHA, False),
    ):
        for word in aliases(key):
            words.setdefault(matching.normalize(word).strip(), (facet, value))
    suffix = matching.normalize("キャラ")
    for index, name in enumerate(rarity_names):
        word = matching.normalize(name).strip()
        # "基本キャラ" can also be typed as "基本"; "Uber Rare" as "uberrare"
        short = word[: -len(suffix)] if word.endswith(suffix) else ""
        for form in (word, "".join(word.split()), short):
            if form:
                words.setdefault(form, (RARITY, index))
    return words


class CatFilter:
    """The picker's custom filter: query -> row indexes to show.

    Rows are ``infos`` (index = position in the save's cat list) with
    ``owned`` flags taken when the picker opens."""

    def __init__(
        self,
        infos: Sequence[CatInfo],
        owned: Sequence[bool],
        words: dict[str, tuple[str, object]],
    ) -> None:
        self.infos = list(infos)
        self.owned = list(owned)
        self.words = words
        self.index_of_id = {info.id: i for i, info in enumerate(self.infos)}
        self.max_id = max(self.index_of_id, default=-1)

    def parse(self, query: str) -> CatQuery:
        parsed = CatQuery()
        seen: set[int] = set()
        for term in _SEPARATORS.split(matching.normalize(query)):
            if not term:
                continue
            id_match = _ID_TERM.fullmatch(term)
            if id_match is not None:
                if parsed.ids is None:
                    parsed.ids = []
                for cat_id in self._ids(id_match):
                    index = self.index_of_id.get(cat_id)
                    if index is not None and index not in seen:
                        seen.add(index)
                        parsed.ids.append(index)
            elif "." in term and _VERSION_TERM.fullmatch(term):
                parsed.versions.append(
                    tuple(int(part) for part in term.rstrip(".").split("."))
                )
            elif term in self.words:
                facet, value = self.words[term]
                parsed.facets.setdefault(facet, set()).add(value)
            else:
                parsed.texts.append(term)
        return parsed

    def _ids(self, id_match: re.Match[str]) -> Iterable[int]:
        first = int(id_match.group(1))
        if id_match.group(2) is None:
            return (first,)
        last = int(id_match.group(2))
        if first <= last:
            return range(first, min(last, self.max_id) + 1)
        return range(min(first, self.max_id), last - 1, -1)

    def value(self, index: int, facet: str) -> object:
        info = self.infos[index]
        if facet == OWNED:
            return self.owned[index]
        if facet == OBTAINABLE:
            return info.obtainable
        if facet == GACHA:
            return info.gacha
        return info.rarity

    def matches(self, index: int, query: CatQuery) -> bool:
        for facet, values in query.facets.items():
            if self.value(index, facet) not in values:
                return False
        info = self.infos[index]
        if query.versions:
            version = info.version
            if version is None or not any(
                version[: len(prefix)] == prefix for prefix in query.versions
            ):
                return False
        return all(text in info.search for text in query.texts)

    def __call__(self, query: str) -> list[int]:
        parsed = self.parse(query)
        if parsed.only_ids():
            return list(parsed.ids or [])
        rows = parsed.ids if parsed.ids is not None else range(len(self.infos))
        return [i for i in rows if self.matches(i, parsed)]


# -- the picker -------------------------------------------------------------------


def _first_alias(key: str) -> str:
    words = aliases(key)
    return words[0] if words else tr(key)


class CatPicker:
    """Rows, filter and texts of the picker for the current state of a save."""

    def __init__(self, save_file: core.SaveFile) -> None:
        self.save_file = save_file
        self.cats = list(save_file.cats.cats)
        self.infos = cat_infos(save_file)
        self.rarity_names = save_file.cats.get_rarity_names(save_file)
        owned = [bool(cat.unlocked) for cat in self.cats]
        self.filter = CatFilter(self.infos, owned, facet_words(self.rarity_names))
        self.not_owned = _first_alias("cats_facet_not_owned")
        no_name = tr("cats_no_name")
        rarities = [self.rarity(info) for info in self.infos]
        states = [self.state(cat) for cat in self.cats]
        # padded to columns: the hint is right-aligned in the row
        rarity_width = max((width(r) for r in rarities), default=0)
        state_width = max((width(s) for s in states), default=0)
        self.choices: list[tui.Choice] = []
        for info, rarity, state in zip(self.infos, rarities, states):
            hint = pad(state, state_width)
            if rarity_width:
                hint = pad(rarity, rarity_width) + "  " + hint
            label = f"{info.id:>3} {escape(info.name or no_name)}"
            self.choices.append(tui.Choice(label, hint=escape(hint)))

    def rarity(self, info: CatInfo) -> str:
        if 0 <= info.rarity < len(self.rarity_names):
            return self.rarity_names[info.rarity]
        return ""

    def state(self, cat: core.Cat) -> str:
        """The level as the upgrade screen shows it, or "not owned"."""
        if not cat.unlocked:
            return self.not_owned
        level = tr("cats_hint_level", base=cat.upgrade.base + 1, plus=cat.upgrade.plus)
        return plain(level)

    def footer(self) -> str:
        names = self.rarity_names
        rarity = names[4] if len(names) > 4 else (names[-1] if names else "")
        return tr(
            "cats_picker_footer",
            not_owned=self.not_owned,
            rarity=rarity,
            version=self.save_file.game_version.to_string(),
        )

    def index_of(self, cats: Iterable[core.Cat]) -> list[int]:
        rows = self.filter.index_of_id
        return sorted({rows[cat.id] for cat in cats if cat.id in rows})


def pick_cats(
    save_file: core.SaveFile,
    preselected: Iterable[core.Cat] = (),
    more: Callable[[], list[core.Cat] | None] | None = None,
) -> list[core.Cat] | None:
    """Let the user check cats; returns them in id order, None if cancelled.

    ``preselected`` cats start checked.  ``more`` runs the selection-method
    menu for Ctrl-O (None when cancelled there: back to the list); its cats
    are added to the checked ones.  Raises :class:`tui.TuiUnavailable` when
    the interactive list cannot run, so that the caller can use the classic
    prompts."""
    picker = CatPicker(save_file)
    cats = picker.cats
    extra_keys = (
        [tui.ExtraKey(MORE, MORE_KEY, plain(tr("cats_picker_more")))]
        if more is not None
        else []
    )
    selected = picker.index_of(preselected)
    query = ""
    while True:
        result = tui.pick(
            tr("cats_picker_title"),
            picker.choices,
            multi=True,
            selected=selected,
            footer=picker.footer(),
            filter=picker.filter,
            extra_keys=extra_keys,
            query=query,
            fallback=False,
        )
        if result is None:
            return None
        if result.key != MORE or more is None:
            return [cats[i] for i in result.indexes]
        checked = list(result.checked)  # not the highlighted row
        added = more()
        if not added:  # cancelled or nothing found: back to the list
            selected, query = checked, result.query
            continue
        ids = {cats[i].id for i in checked} | {cat.id for cat in added}
        chosen = [cat for cat in cats if cat.id in ids]
        color.color_print(summary(save_file, chosen))
        return chosen


def summary(save_file: core.SaveFile, cats: Sequence[core.Cat]) -> str:
    """One line naming the selected cats (as many as fit the terminal)."""
    if not cats:
        return tr("cats_selected_none")
    try:
        infos = cat_infos(save_file)
    except Exception:  # no game data: ids only
        infos = []
    names = {info.id: info.name for info in infos}
    no_name = plain(tr("cats_no_name"))
    items = [f"{cat.id} {names.get(cat.id) or no_name}" for cat in cats]
    columns = max(40, shutil.get_terminal_size((100, 30)).columns - 1)
    line = ""
    for shown in range(1, len(items) + 1):
        text = " / ".join(items[:shown])
        rest = len(items) - shown
        if rest:
            text += " " + plain(tr("cats_selected_more", count=rest))
        candidate = tr("cats_selected_summary", count=len(items), names=text)
        if line and width(plain(candidate)) > columns:
            break
        line = candidate
    return line


# -- level input --------------------------------------------------------------------

_MAX_WORDS = re.compile(r"max|最大|さいだい|上限", re.IGNORECASE)


def clean_upgrade_input(text: str, max_text: str) -> str:
    """Upgrade text typed with a Japanese IME -> the editor's format:
    full-width characters, ``〜`` / ``ー`` as range dashes, spaces and the
    words 最大 / MAX for the maximum."""
    value = "".join(unicodedata.normalize("NFKC", text).split())
    for dash in _DASHES:
        value = value.replace(dash, "-")
    return _MAX_WORDS.sub(max_text, value)


def upgrade_input(
    max_base: int, max_plus: int, count: int | None = None
) -> tuple[core.Upgrade | None, bool]:
    """Interactive form of :meth:`core.Upgrade.get_user_upgrade`: one text
    field with a one-line example instead of the long help; Enter checks the
    text with the same parser and shows its error under the field (empty text
    is refused: the classic prompt would read it as "+0").  ``count`` is the
    number of cats the level is for (None: one cat or skill).  Esc or the
    quit key cancel.  Raises :class:`tui.TuiUnavailable`."""
    quit_key = core.localize("quit_key")
    max_text = core.localize("max")

    def parse(text: str) -> core.Upgrade | str | None:
        """The upgrade, an error message, or None for "cancel"."""
        value = clean_upgrade_input(text, max_text)
        if value == quit_key:
            return None
        if not value:
            return tui_strings.tr("tui_error_empty")
        return core.Upgrade.parse_user_upgrade(value, max_base, max_plus)

    def validate(text: str) -> str | None:
        result = parse(text)
        return result if isinstance(result, str) else None

    levels = {"max_base": max_base + 1, "max_plus": max_plus}
    if count is None:
        title = tr("cats_upgrade_title", **levels)
    else:
        title = tr("cats_upgrade_title_many", count=count, **levels)
    text = tui.text(
        tr("cats_upgrade_hint") + "\n" + title, validate=validate, fallback=False
    )
    if text is None:
        return None, True
    result = parse(text)
    if result is None:
        return None, True
    if isinstance(result, str):  # not reached: the field validated it
        return None, False
    return result, False
