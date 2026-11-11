"""The home menu without a terminal: rows built from the feature tree, the
"frequently used" section, filtering, the usage file and the loop.

The editor's core is initialised in-process on a private data folder (a copy
of the packaged files, no game data, an unroutable proxy); ``tui.pick`` and
the exit flow are replaced where the loop is tested.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import shutil
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

import bcsfe
from bcsfe import core
from bcsfe.cli import feature_handler, home, home_text, home_usage, main, tui
from bcsfe.cli.tui import matching
from tests.conftest import FIXTURE_SAVE
from tests.test_locale_ja import parse_entries

FILES = pathlib.Path(bcsfe.__file__).parent / "files"
_CORE_GLOBALS = (
    "config_path",
    "print_config_err",
    "log_path",
    "transfer_backup_path",
    "game_data_path",
    "data_dir_path",
)
_ENV_DIRS = (
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
)
_NO_NETWORK = "http://127.0.0.1:9"

StartCore = Callable[..., pathlib.Path]


@pytest.fixture
def start_core(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StartCore]:
    """``start_core(locale="ja", name="core", **config)`` initialises core_data
    on a new private root (data folder named ``data`` below it) and returns
    the root.  Paths, environment and core_data are restored afterwards."""
    with core_starter(tmp_path_factory, monkeypatch) as start:
        yield start


@contextlib.contextmanager
def core_starter(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StartCore]:
    """The body of the ``start_core`` fixture, for the other test_home* modules."""
    saved_globals = {name: getattr(core, name) for name in _CORE_GLOBALS}
    saved_state = dict(core.core_data.__dict__)

    def start(locale: str = "ja", name: str = "core", **config: Any) -> pathlib.Path:
        root = tmp_path_factory.mktemp(name)
        for var in _ENV_DIRS:
            (root / var.lower()).mkdir()
            monkeypatch.setenv(var, str(root / var.lower()))
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            monkeypatch.setenv(var, _NO_NETWORK)
        shutil.copytree(FILES, root / "data")
        lines = [f"locale: {locale}"] + [f"{k}: {v}" for k, v in config.items()]
        (root / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        core.set_data_dir_path(core.Path(str(root / "data")))
        core.set_config_path(core.Path(str(root / "config.yaml")))
        core.set_log_path(core.Path(str(root / "bcsfe.log")))
        core.set_game_data_path(core.Path(str(root / "game_data")))
        core.core_data.init_data()
        return root

    try:
        yield start
    finally:
        for name, value in saved_globals.items():
            setattr(core, name, value)
        core.core_data.__dict__.clear()
        core.core_data.__dict__.update(saved_state)


def fixture_save() -> core.SaveFile:
    if not FIXTURE_SAVE.is_file():
        pytest.skip(f"fixture save not found: {FIXTURE_SAVE}")
    return core.SaveFile(core.Path(str(FIXTURE_SAVE)).read())


class _NoSave:
    """Stands in for the save where only the feature tree is needed."""


def categories() -> list[home.Category]:
    handler = feature_handler.FeatureHandler(_NoSave())  # type: ignore[arg-type]
    return home.collect(handler.get_features())


def features_by_name(cats: list[home.Category]) -> dict[str, home.Feature]:
    found: dict[str, home.Feature] = {}
    for category in cats:
        for feature in category.features:
            found.setdefault(feature.name, feature)
    return found


def labels(menu: home.Menu, indexes: list[int]) -> list[str]:
    return [matching.strip_markup(menu.choices[i].label) for i in indexes]


# ------------------------------------------------------------------- feature rows


def test_categories_follow_the_feature_tree(start_core: StartCore) -> None:
    start_core("ja")
    handler = feature_handler.FeatureHandler(_NoSave())  # type: ignore[arg-type]
    tree = handler.get_features()

    cats = home.collect(tree)

    dict_keys = [k for k, v in tree.items() if isinstance(v, dict)]
    assert [c.label for c in cats[:-1]] == [home.display_name(k) for k in dict_keys]
    assert cats[0].label == "セーブデータの管理"
    assert cats[1].label == "アイテム"
    # every leaf once, in upstream order; the top-level exit has no heading
    for category, key in zip(cats, dict_keys):
        assert [f.path for f in category.features] == [
            (key, leaf) for leaf in tree[key]
        ]
    assert cats[-1].label == ""
    assert [f.path for f in cats[-1].features] == [("exit",)]
    assert cats[-1].features[0].exits
    assert cats[-1].features[0].label == "終了"


def test_row_label_hint_and_keywords(start_core: StartCore) -> None:
    start_core("ja")
    found = features_by_name(categories())

    xp = found["xp"]
    assert (xp.label, xp.hint, xp.path) == ("XP", "アイテム", ("items", "xp"))
    catfood = found["catfood"]
    assert catfood.label == "ネコカン"
    # aliases, the English name, the dict key and the category names
    for word in ("Cat Food", "catfood", "アイテム", "Items"):
        assert word in catfood.keywords
    tickets = found["rare_tickets"].keywords
    assert "Rare Tickets" in tickets and "Gold Tickets" in tickets
    # a ja name with an English alias: the label is the first alias
    talents = found["talent_orbs"]
    assert talents.label == "本能玉"
    assert "Talent Orbs" in talents.keywords
    upload = found["save_upload"]
    assert upload.label.startswith("セーブデータをサーバーにアップロード")
    assert upload.hint == "セーブデータの管理"
    about = found["home_about"]
    assert (about.label, about.hint) == ("このエディターについて", "エディター")
    assert "About this editor" in about.keywords
    exit_row = found["exit"]
    assert exit_row.hint == "" and "Exit" in exit_row.keywords


def test_row_names_in_english(start_core: StartCore) -> None:
    start_core("en")
    found = features_by_name(categories())

    assert found["xp"].label == "XP"
    assert found["catfood"].label == "Cat Food"
    assert found["rare_tickets"].label == "Rare Tickets"  # first alias
    assert "Gold Tickets" in found["rare_tickets"].keywords
    assert found["catfood"].hint == "Items"
    assert found["home_about"].label == "About this editor"


def test_localised_dict_key_is_shown_like_upstream_but_its_path_does_not_match(
    start_core: StartCore,
) -> None:
    # the data folder path contains a word no feature name has
    root = start_core("ja", name="zqxv")
    cats = categories()
    found = features_by_name(cats)

    documents = found["save_save_documents"]  # name found from the template
    shown = matching.strip_markup(documents.label)
    assert shown == f"セーブデータを {root / 'data' / 'saves' / 'SAVE_DATA'} に保存"
    assert "zqxv" not in documents.match_label + documents.keywords
    menu = home.build_menu(cats, [])
    assert menu.filter("zqxv") == []
    assert shown in labels(menu, menu.filter("save to"))  # English template words
    # the dict key (with the path) still reaches the feature
    assert (
        documents.path[-1]
        in feature_handler.FeatureHandler(
            _NoSave()  # type: ignore[arg-type]
        ).get_features()["save_management"]
    )


def test_template_key_needs_a_real_literal_part(start_core: StartCore) -> None:
    start_core("en")
    assert (
        home.template_key("Save save to /somewhere/SAVE_DATA") == "save_save_documents"
    )
    assert home.template_key("no such feature name") is None


def test_missing_home_keys_fall_back_to_english(start_core: StartCore) -> None:
    root = start_core("ja")
    for lang in ("en", "ja"):  # an old data folder without fork/home.properties
        (root / "data" / "locales" / lang / "fork" / "home.properties").unlink()
    core.core_data.init_data()

    assert home_text.tr("home_prompt") == "Select a feature:"
    assert home.display_name("home_about") == "About this editor"
    header = home_text.tr("home_start_header", version="9.9.9+jp.9")
    assert '"About this editor"' in header and "9.9.9+jp.9" in header


def test_fallback_texts_match_the_english_locale() -> None:
    path = FILES / "locales" / "en" / "fork" / "home.properties"
    en = {e.key: e.value for e in parse_entries(path.read_text(encoding="utf-8"))}

    assert en == home_text.FALLBACK


# ----------------------------------------------------------------- menu and filter


def test_menu_shows_favourites_then_categories(start_core: StartCore) -> None:
    start_core("ja")
    cats = categories()

    menu = home.build_menu(cats, ["xp", "np", "no_such_feature"])

    shown = labels(menu, menu.filter(""))
    assert shown[:4] == ["【よく使う】", "XP", "NP", "【セーブデータの管理】"]
    assert menu.choices[0].heading and not menu.choices[1].heading
    assert menu.targets[0] is None
    assert menu.targets[1] is not None and menu.targets[1].path == ("items", "xp")
    # a favourite also stays in its category while browsing
    assert shown.count("XP") == 2
    # a blank separator, then exit, at the end
    assert shown[-2:] == ["", "終了"] and menu.choices[-2].heading
    assert sum(1 for c in menu.choices if c.heading) == 1 + len(cats)


def test_no_favourites_no_favourites_heading(start_core: StartCore) -> None:
    start_core("ja")

    menu = home.build_menu(categories(), [])

    assert labels(menu, menu.filter(""))[0] == "【セーブデータの管理】"


def test_query_lists_each_feature_once(start_core: StartCore) -> None:
    start_core("ja")
    menu = home.build_menu(categories(), ["xp", "np"])

    for query in ("xp", "ＸＰ", " xp "):
        rows = menu.filter(query)
        assert labels(menu, rows)[0] == "XP"
        assert labels(menu, rows).count("XP") == 1
        assert all(not menu.choices[i].heading for i in rows)
        xp = menu.targets[rows[0]]
        assert xp is not None and xp.path == ("items", "xp")
        assert rows[0] > 3  # the row in its category, not the favourite


def test_query_matches_kana_english_and_category(start_core: StartCore) -> None:
    start_core("ja")
    menu = home.build_menu(categories(), [])

    assert labels(menu, menu.filter("ねこかん")) == ["ネコカン"]
    assert labels(menu, menu.filter("cat food")) == ["ネコカン"]
    assert labels(menu, menu.filter("talent orbs"))[0] == "本能玉"
    assert labels(menu, menu.filter("about")) == ["このエディターについて"]
    assert labels(menu, menu.filter("exit")) == ["終了"]
    # a category name lists the features of that category
    gacha = labels(menu, menu.filter("ガチャ"))
    seeds = [
        "レアガチャのシード値",
        "にゃんこガチャのシード値",
        "イベントガチャのシード値",
    ]
    assert all(seed in gacha for seed in seeds)
    assert "XP" not in gacha
    # features listed in two categories appear once
    skills = labels(menu, menu.filter("special skills"))
    assert len(skills) == len(set(skills))


def test_seeds_skip_features_that_do_not_exist(start_core: StartCore) -> None:
    start_core("ja", separate_cat_edit_options="false")
    found = features_by_name(categories())

    assert "cats" in found and "unlock_remove_cats" not in found
    assert home_usage.Usage(core.Path("unused")).favourites(set(found)) == [
        "save_upload",
        "xp",
        "np",
        "catfruit",
    ]


# ------------------------------------------------------------------------- usage


def make_usage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> home_usage.Usage:
    """A usage file below tmp_path and a clock that ticks once per use."""
    clock = iter(float(t) for t in range(1000, 2000))
    monkeypatch.setattr(home_usage, "time", SimpleNamespace(time=lambda: next(clock)))
    return home_usage.Usage(core.Path(str(tmp_path / "fork" / "usage.json")))


ALL = {"save_upload", "unlock_remove_cats", "upgrade_cats", "xp", "np", "catfruit"}
MORE = ALL | {"catfood", "catseyes", "talent_orbs", "rare_tickets"}


def test_seeds_until_something_is_used(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = make_usage(tmp_path, monkeypatch)

    assert usage.favourites(MORE) == list(home_usage.SEEDS)


def test_favourites_rank_by_count_then_recency(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = make_usage(tmp_path, monkeypatch)
    for name in ("catseyes", "talent_orbs", "talent_orbs", "catfood", "xp"):
        usage.record(name)

    # used ones first (count, then the most recent), then seeds fill up to 6
    assert usage.favourites(MORE) == [
        "talent_orbs",
        "xp",
        "catfood",
        "catseyes",
        "save_upload",
        "unlock_remove_cats",
    ]
    for name in ("rare_tickets", "np", "upgrade_cats"):
        usage.record(name)
    assert usage.favourites(MORE) == [
        "talent_orbs",
        "upgrade_cats",
        "np",
        "rare_tickets",
        "xp",
        "catfood",
    ]
    # only features of the current tree
    assert usage.favourites({"catseyes", "np"}) == ["np", "catseyes"]


def test_usage_is_saved_and_loaded(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = make_usage(tmp_path, monkeypatch)
    usage.record("xp")
    usage.record("xp")
    usage.record("np")

    path = tmp_path / "fork" / "usage.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["features"]["xp"]["count"] == 2
    assert not list(path.parent.glob("*.partial"))
    loaded = home_usage.Usage.load(core.Path(str(path)))
    assert loaded.favourites(MORE)[:2] == ["xp", "np"]
    assert loaded.entries["xp"].count == 2


def test_default_usage_file_is_in_the_data_folder(start_core: StartCore) -> None:
    root = start_core("ja")

    home_usage.Usage.load().record("xp")

    assert (root / "data" / "fork" / "usage.json").is_file()


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "{not json",
        "[1, 2]",
        '{"features": [1]}',
        '{"features": {"xp": "many"}}',
        '{"features": {"xp": {"count": "3"}}}',
        '{"features": {"xp": {"count": 0, "last": 1}}}',
        '{"features": {"xp": {"count": true, "last": 1}}}',
    ],
)
def test_missing_or_corrupt_usage_file_is_ignored(
    tmp_path: pathlib.Path, content: str | None
) -> None:
    path = tmp_path / "usage.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    usage = home_usage.Usage.load(core.Path(str(path)))

    assert usage.entries == {}
    assert usage.favourites(MORE) == list(home_usage.SEEDS)


def test_valid_entries_survive_a_partly_corrupt_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text(
        '{"features": {"xp": {"count": 2, "last": 5}, "np": {"count": -1}}}',
        encoding="utf-8",
    )

    usage = home_usage.Usage.load(core.Path(str(path)))

    assert list(usage.entries) == ["xp"]


def test_failed_write_keeps_the_old_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = make_usage(tmp_path, monkeypatch)
    usage.record("xp")
    path = tmp_path / "fork" / "usage.json"
    before = path.read_text(encoding="utf-8")

    def fail(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(home_usage, "os", SimpleNamespace(replace=fail))
    usage.record("np")  # does not raise

    assert path.read_text(encoding="utf-8") == before


# ------------------------------------------------------------------------- title


def test_title_summarises_the_save(start_core: StartCore) -> None:
    start_core("ja")
    save = fixture_save()

    lines = [matching.strip_markup(line) for line in home.title(save).splitlines()]

    code = save.inquiry_code
    unlocked = len(save.cats.get_unlocked_cats())
    assert lines[0] == (
        f"jp 16.0.0  お問い合わせコード {code[:4]}***{code[-2:]}"
        f"  キャラ {unlocked:,} / {len(save.cats.cats):,}"
    )
    assert lines[1] == (
        f"ネコカン {save.catfood:,}  XP {save.xp:,}  NP {save.np:,}"
        f"  レアチケット {save.rare_tickets:,}"
        f"  プラチナチケット {save.platinum_tickets:,}"
        f"  レジェンドチケット {save.legend_tickets:,}"
    )
    assert lines[2] == "機能を選んでください:"
    save.xp = 12345678
    assert "XP 12,345,678" in matching.strip_markup(home.title(save))


# -------------------------------------------------------------------------- loop


class Recorder:
    """Scripted ``tui.pick`` answers and records of what the loop did."""

    def __init__(self, answers: list[str | None]) -> None:
        self.answers = answers  # a label to choose, or None for Esc
        self.titles: list[str] = []
        self.features: list[list[str]] = []
        self.exits: list[tuple[Any, bool]] = []

    def pick(self, title: str, choices: list[tui.Choice], **kwargs: Any) -> Any:
        assert kwargs["fallback"] is False
        self.titles.append(title)
        answer = self.answers.pop(0)
        if answer is None:
            return None
        rows = kwargs["filter"]("")
        index = next(
            i
            for i in rows
            if not choices[i].heading
            and matching.strip_markup(choices[i].label) == answer
        )
        return tui.ListResult((index,))


def run_home(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[str | None],
    exit_cancels: bool = True,
) -> tuple[Recorder, home.Home]:
    save = fixture_save()
    handler = feature_handler.FeatureHandler(save)
    recorder = Recorder(answers)

    def interactive() -> bool:
        return bool(recorder.answers)

    def run_feature(path: list[str]) -> None:
        recorder.features.append(path)
        if path == ["items", "xp"]:
            save.xp = 4321

    def exit_editor(
        save_file: Any = None, check_temp: bool = True, cancellable: bool = False
    ) -> None:
        recorder.exits.append((save_file, cancellable))
        if not exit_cancels:
            raise SystemExit

    monkeypatch.setattr(tui, "is_interactive", interactive)
    monkeypatch.setattr(tui, "pick", recorder.pick)
    monkeypatch.setattr(handler, "run_feature", run_feature)
    monkeypatch.setattr(main.Main, "exit_editor", staticmethod(exit_editor))
    loop = home.Home(handler)
    loop.run()
    return recorder, loop


def test_enter_runs_the_chosen_row_then_home_again(
    start_core: StartCore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = start_core("ja")
    capsys.readouterr()

    recorder, _ = run_home(monkeypatch, ["XP", "NP"])

    assert recorder.features == [["items", "xp"], ["items", "np"]]
    # the summary is rebuilt after every feature
    assert "XP 4,321" not in matching.strip_markup(recorder.titles[0])
    assert "XP 4,321" in matching.strip_markup(recorder.titles[1])
    # a blank line before every home
    assert capsys.readouterr().out == "\n\n"
    usage = json.loads((root / "data" / "fork" / "usage.json").read_text("utf-8"))
    assert {k: v["count"] for k, v in usage["features"].items()} == {"xp": 1, "np": 1}


def test_esc_and_the_exit_row_start_the_cancellable_exit_flow(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = start_core("ja")

    recorder, loop = run_home(monkeypatch, [None, "終了", "XP"])

    save = loop.handler.save_file
    assert recorder.exits == [(save, True), (save, True)]
    assert recorder.features == [["items", "xp"]]  # exit is not run as a feature
    usage = json.loads((root / "data" / "fork" / "usage.json").read_text("utf-8"))
    assert list(usage["features"]) == ["xp"]  # exit is not counted


def test_exit_that_leaves_ends_the_loop(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_core("ja")

    with pytest.raises(SystemExit):
        run_home(monkeypatch, [None, "XP"], exit_cancels=False)


def test_about_is_not_counted(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = start_core("ja")

    recorder, _ = run_home(monkeypatch, ["このエディターについて"])

    assert recorder.features == [["editor", "home_about"]]
    assert not (root / "data" / "fork" / "usage.json").exists()


def test_home_hands_over_when_the_list_cannot_start(
    start_core: StartCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_core("ja")
    handler = feature_handler.FeatureHandler(fixture_save())

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise tui.TuiUnavailable("no terminal")

    monkeypatch.setattr(tui, "is_interactive", lambda: True)
    monkeypatch.setattr(tui, "pick", unavailable)

    home.Home(handler).run()  # returns: the classic menu takes over
