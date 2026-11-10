"""Regression test for GameDataGetter.delete_old_versions (fixed in 6f8906d).

It used to narrow ``to_keep`` inside the region loop, so an empty region visited first
(en) made every later region keep nothing: all jp game data was wiped on each start.
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import pytest

from bcsfe import core
from bcsfe.core.server.game_data_getter import GameDataGetter

JP_VERSIONS = ["15.4.0", "15.5.0", "15.6.0", "15.7.2", "15.8.0", "15.9.0", "15.10.0"]
KR_VERSIONS = ["15.6.0", "15.7.0"]


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def make_version(game_data: pathlib.Path, cc: str, version: str) -> None:
    path = game_data / cc / version
    (path / "DataLocal").mkdir(parents=True)
    (path / "DataLocal" / "unitbuy.csv").write_text(version)
    (path / "downloaded").write_bytes(b"")


def remaining(game_data: pathlib.Path, cc: str) -> list[str]:
    region = game_data / cc
    if not region.is_dir():
        return []
    return sorted((p.name for p in region.iterdir() if p.is_dir()), key=version_key)


@pytest.fixture
def game_data(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pathlib.Path]:
    """A temp game data dir; the editor's paths are restored afterwards."""
    for var in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    saved = (core.game_data_path, core.data_dir_path)
    path = tmp_path / "game_data"
    path.mkdir()
    core.set_game_data_path(core.Path(str(path)))
    core.set_data_dir_path(core.Path(str(tmp_path / "data")))
    try:
        # never let the cleanup run against a real directory
        assert GameDataGetter.get_game_data_dir().to_str() == str(path)
        yield path
    finally:
        core.game_data_path, core.data_dir_path = saved


@pytest.mark.parametrize("en_versions", [[], ["15.7.0"]])
def test_delete_old_versions_keeps_newest_five_per_region(
    game_data: pathlib.Path, en_versions: list[str]
) -> None:
    for cc, versions in (("jp", JP_VERSIONS), ("kr", KR_VERSIONS), ("en", en_versions)):
        for version in versions:
            make_version(game_data, cc, version)
    (game_data / "en").mkdir(exist_ok=True)
    (game_data / "tw").mkdir()

    GameDataGetter.delete_old_versions(5)

    # numeric order: 15.10.0 is the newest, 15.4.0 and 15.5.0 are the oldest two
    assert remaining(game_data, "jp") == [
        "15.6.0",
        "15.7.2",
        "15.8.0",
        "15.9.0",
        "15.10.0",
    ]
    assert remaining(game_data, "kr") == KR_VERSIONS
    assert remaining(game_data, "en") == en_versions
    assert remaining(game_data, "tw") == []
    for version in remaining(game_data, "jp"):
        assert (
            game_data / "jp" / version / "DataLocal" / "unitbuy.csv"
        ).read_text() == version


def test_versions_without_marker_are_not_counted_or_deleted(
    game_data: pathlib.Path,
) -> None:
    for version in JP_VERSIONS[:6]:
        make_version(game_data, "jp", version)
    (game_data / "jp" / "16.0.0" / "DataLocal").mkdir(parents=True)  # incomplete

    GameDataGetter.delete_old_versions(5)

    assert remaining(game_data, "jp") == [
        "15.5.0",
        "15.6.0",
        "15.7.2",
        "15.8.0",
        "15.9.0",
        "16.0.0",
    ]
