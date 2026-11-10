"""Tests for building jp game data from the official app package (WP3).

Synthetic packages are encrypted here with the same scheme as the real ones; the network
is always mocked. The end-to-end test uses a cached real package when it is present.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import re
import tarfile
import types
import zipfile
from importlib import resources
from typing import Any, Iterator

import pytest
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import bcsfe
from bcsfe import core
from bcsfe.cli import color, dialog_creator
from bcsfe.core.server import game_data_builder as gdb
from bcsfe.core.server import game_data_getter
from bcsfe.core.server.game_data_getter import GameDataGetter

REPO = pathlib.Path(__file__).resolve().parents[1]
REAL_PACKAGE = pathlib.Path(
    os.environ.get("BCSFE_TEST_PACKAGE", REPO.parent / "cache" / "jp-16.0.0.xapk")
)
REAL_REFERENCE = pathlib.Path(
    os.environ.get(
        "BCSFE_TEST_REFERENCE",
        REPO.parent / "fixtures" / "game_data" / "jp" / "16.0.0",
    )
)

DATA_FILES = {
    "unitbuy.csv": b"1,2,3\n4,5,6\n",
    "Unit_Explanation1_ja.csv": "ネコ|説明\n".encode(),
    "empty_block.csv": b"0123456789abcdef",  # exactly one AES block before padding
}
RES_FILES = {"Localizable.tsv": b"key\tvalue\n"}

_CORE_GLOBALS = (
    "config_path",
    "print_config_err",
    "log_path",
    "transfer_backup_path",
    "game_data_path",
    "data_dir_path",
)


# --------------------------------------------------------------------------- helpers


def _pad(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()


def encrypt_list_bytes(raw: bytes, pad: bool = True) -> bytes:
    encryptor = Cipher(algorithms.AES(gdb.LIST_KEY), modes.ECB()).encryptor()
    return encryptor.update(_pad(raw) if pad else raw) + encryptor.finalize()


def encrypt_list(text: str) -> bytes:
    return encrypt_list_bytes(text.encode())


def encrypt_entry(data: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(gdb.PACK_KEY), modes.CBC(gdb.PACK_IV)).encryptor()
    return encryptor.update(_pad(data)) + encryptor.finalize()


def make_pack(files: dict[str, bytes]) -> tuple[bytes, bytes]:
    """Return (encrypted .list, .pack) for ``files``, like the game ships them."""
    pack = b""
    lines: list[str] = []
    for name, content in files.items():
        blob = encrypt_entry(content)
        lines.append(f"{name},{len(pack)},{len(blob)}")
        pack += blob
    return encrypt_list(f"{len(lines)}\n" + "\n".join(lines) + "\n"), pack


def make_apk(packs: dict[str, tuple[bytes, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as apk:
        apk.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00")
        for pack, (list_data, pack_data) in packs.items():
            apk.writestr(f"assets/{pack}.list", list_data)
            apk.writestr(f"assets/{pack}.pack", pack_data)
    return buffer.getvalue()


def default_packs() -> dict[str, tuple[bytes, bytes]]:
    return {"DataLocal": make_pack(DATA_FILES), "resLocal": make_pack(RES_FILES)}


def make_xapk(
    path: pathlib.Path,
    packs: dict[str, tuple[bytes, bytes]] | None = None,
    version: str | None = "16.0.0",
    package_name: str = gdb.PACKAGE_NAME,
    compression: int = zipfile.ZIP_STORED,
) -> pathlib.Path:
    """An XAPK: a zip of split APKs with the packs in a secondary one."""
    with zipfile.ZipFile(path, "w", compression=compression) as xapk:
        xapk.writestr(f"{gdb.PACKAGE_NAME}.apk", make_apk({}))
        xapk.writestr("InstallPack.apk", make_apk(packs or default_packs()))
        xapk.writestr("icon.png", b"\x89PNG")
        if version is not None:
            manifest = {"package_name": package_name, "version_name": version}
            xapk.writestr("manifest.json", json.dumps(manifest))
    return path


def tree(root: pathlib.Path) -> dict[str, pathlib.Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def assert_installed(dest: pathlib.Path) -> None:
    files = tree(dest)
    expected = {f"DataLocal/{name}" for name in DATA_FILES}
    expected |= {f"resLocal/{name}" for name in RES_FILES}
    expected.add(gdb.MARKER)
    assert set(files) == expected
    for name, content in DATA_FILES.items():
        assert (dest / "DataLocal" / name).read_bytes() == content
    for name, content in RES_FILES.items():
        assert (dest / "resLocal" / name).read_bytes() == content
    assert (dest / gdb.MARKER).read_bytes() == b""


def leftovers(region_dir: pathlib.Path) -> list[str]:
    if not region_dir.exists():
        return []
    return sorted(p.name for p in region_dir.iterdir() if p.name.startswith("."))


class Recorder:
    """Collects ``status(key, **kwargs)`` calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, key: str, /, **kwargs: Any) -> None:
        self.calls.append((key, kwargs))

    def keys(self) -> list[str]:
        return [key for key, _ in self.calls]


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]


class FakeNetwork:
    """Stands in for ``requests.get``; unknown URLs fail the test."""

    def __init__(self, routes: dict[str, FakeResponse | Exception]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if url not in self.routes:
            raise AssertionError(f"unexpected request: {url}")
        route = self.routes[url]
        if isinstance(route, Exception):
            raise route
        return route


PACKAGE_URL = "https://download.invalid/jp-16.0.0.xapk"


def index_json(entries: list[dict[str, Any]]) -> bytes:
    return json.dumps({"version_list": entries}).encode()


def index_entry(
    version: str, url: str, sha256: str | None = None, size: int | None = None
) -> dict[str, Any]:
    asset: dict[str, Any] = {"url": url}
    if sha256 is not None:
        asset["file_sha256"] = sha256
    if size is not None:
        asset["size"] = size
    return {"version_name": version, "version_code": 1, "asset": asset}


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the network must not be used here")

    monkeypatch.setattr(gdb.requests, "get", fail)


@pytest.fixture
def editor_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pathlib.Path]:
    """Point every editor path (and HOME/XDG) at tmp_path and initialise core_data."""
    for var in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        path = tmp_path / var.lower()
        path.mkdir()
        monkeypatch.setenv(var, str(path))
    monkeypatch.delenv(gdb.PACKAGE_ENV, raising=False)
    saved_globals = {name: getattr(core, name) for name in _CORE_GLOBALS}
    saved_state = dict(core.core_data.__dict__)
    (tmp_path / "data").mkdir()
    core.set_data_dir_path(core.Path(str(tmp_path / "data")))
    core.set_config_path(core.Path(str(tmp_path / "config.yaml")))
    core.set_log_path(core.Path(str(tmp_path / "bcsfe.log")))
    core.set_game_data_path(core.Path(str(tmp_path / "game_data")))
    core.core_data.init_data()
    try:
        yield tmp_path
    finally:
        for name, value in saved_globals.items():
            setattr(core, name, value)
        core.core_data.__dict__.clear()
        core.core_data.__dict__.update(saved_state)


# ------------------------------------------------------- (a) extraction from packages


def test_install_from_xapk(tmp_path: pathlib.Path) -> None:
    package = make_xapk(tmp_path / "game.xapk")
    status = Recorder()

    dest = gdb.install_from_package(
        package, tmp_path / "game_data", "16.0.0", status=status
    )

    assert dest == tmp_path / "game_data" / "jp" / "16.0.0"
    assert_installed(dest)
    assert leftovers(dest.parent) == []
    assert status.keys() == [
        "gdb_status_extract",
        "gdb_status_extracted",
        "gdb_status_extracted",
    ]
    assert status.calls[1][1] == {"pack": "DataLocal", "files": len(DATA_FILES)}


def test_install_when_outer_zip_is_a_plain_apk(tmp_path: pathlib.Path) -> None:
    package = tmp_path / "game.apk"
    package.write_bytes(make_apk(default_packs()))

    dest = gdb.install_from_package(package, tmp_path / "game_data", "16.0.0")

    assert_installed(dest)


def test_install_from_xapk_with_compressed_split_apks(tmp_path: pathlib.Path) -> None:
    package = make_xapk(tmp_path / "game.xapk", compression=zipfile.ZIP_DEFLATED)

    assert_installed(gdb.install_from_package(package, tmp_path / "gd", "16.0.0"))


@pytest.mark.parametrize(
    "name",
    [
        "../evil.csv",
        "..",
        ".",
        "",
        "sub/evil.csv",
        "..\\evil.csv",
        "evil\0.csv",
        "/abs.csv",
    ],
)
def test_suspicious_file_name_is_refused(tmp_path: pathlib.Path, name: str) -> None:
    files = {"unitbuy.csv": b"fine", name: b"payload"}
    packs = {"DataLocal": make_pack(files), "resLocal": make_pack(RES_FILES)}
    package = make_xapk(tmp_path / "game.xapk", packs)
    game_data = tmp_path / "game_data"

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, game_data, "16.0.0")

    assert info.value.key == "gdb_reason_bad_name"
    assert not (game_data / "jp" / "16.0.0").exists()
    assert leftovers(game_data / "jp") == []
    written = [
        p for p in tmp_path.rglob("*") if p.is_file() and p.read_bytes() == b"payload"
    ]
    assert written == []


def _bad_lists() -> dict[str, bytes]:
    return {
        "not_whole_blocks": b"\x01" * 20,
        "bad_padding": encrypt_list_bytes(b"\x00" * 32, pad=False),
        "not_utf8": encrypt_list_bytes(b"1\n\xff\xfe.csv,0,16\n"),
        "malformed_line": encrypt_list("1\nunitbuy.csv;0;16\n"),
        "negative_offset": encrypt_list("1\nunitbuy.csv,-16,16\n"),
        "count_mismatch": encrypt_list("3\nunitbuy.csv,0,16\n"),
    }


@pytest.mark.parametrize("case", sorted(_bad_lists()))
def test_bad_list_is_refused(tmp_path: pathlib.Path, case: str) -> None:
    _, pack_data = make_pack({"unitbuy.csv": b"x"})
    packs = {
        "DataLocal": (_bad_lists()[case], pack_data),
        "resLocal": make_pack(RES_FILES),
    }
    package = make_xapk(tmp_path / "game.xapk", packs)

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, tmp_path / "gd", "16.0.0")

    assert info.value.key == "gdb_reason_bad_list"
    assert info.value.kwargs["pack"] == "DataLocal"
    assert not (tmp_path / "gd" / "jp" / "16.0.0").exists()
    assert leftovers(tmp_path / "gd" / "jp") == []


def test_entry_outside_the_pack_is_refused(tmp_path: pathlib.Path) -> None:
    list_data = encrypt_list("1\nunitbuy.csv,0,4096\n")
    packs = {
        "DataLocal": (list_data, encrypt_entry(b"short")),
        "resLocal": make_pack(RES_FILES),
    }
    package = make_xapk(tmp_path / "game.xapk", packs)

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, tmp_path / "gd", "16.0.0")

    assert info.value.key == "gdb_reason_bad_entry"
    assert leftovers(tmp_path / "gd" / "jp") == []


def test_missing_packs_are_reported(tmp_path: pathlib.Path) -> None:
    package = make_xapk(tmp_path / "game.xapk", {"DataLocal": make_pack(DATA_FILES)})

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, tmp_path / "gd", "16.0.0")

    assert (info.value.key, info.value.kwargs) == (
        "gdb_reason_packs_missing",
        {"packs": "resLocal"},
    )


def test_not_a_package(tmp_path: pathlib.Path) -> None:
    package = tmp_path / "game.xapk"
    package.write_bytes(b"this is not a zip file")

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, tmp_path / "gd", "16.0.0")

    assert info.value.key == "gdb_reason_package"


def test_missing_package_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(tmp_path / "nope.xapk", tmp_path / "gd", "16.0.0")

    assert info.value.key == "gdb_reason_missing_file"


def test_manifest_must_match_version_and_app(tmp_path: pathlib.Path) -> None:
    older = make_xapk(tmp_path / "older.xapk", version="15.7.2")
    other_app = make_xapk(tmp_path / "other.xapk", package_name="jp.example.other")

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(older, tmp_path / "gd", "16.0.0")
    assert (info.value.key, info.value.kwargs) == (
        "gdb_reason_version_mismatch",
        {"found": "15.7.2", "version": "16.0.0"},
    )

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(other_app, tmp_path / "gd", "16.0.0")
    assert info.value.key == "gdb_reason_wrong_package"
    assert not (tmp_path / "gd" / "jp" / "16.0.0").exists()


def test_complete_install_is_kept_and_incomplete_one_replaced(
    tmp_path: pathlib.Path,
) -> None:
    package = make_xapk(tmp_path / "game.xapk")
    game_data = tmp_path / "game_data"
    dest = game_data / "jp" / "16.0.0"
    (dest / "DataLocal").mkdir(parents=True)
    (dest / "DataLocal" / "stale.csv").write_bytes(b"from an interrupted download")

    gdb.install_from_package(package, game_data, "16.0.0")

    assert_installed(dest)  # the stale file is gone, no marker was reused
    assert leftovers(game_data / "jp") == []
    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, game_data, "16.0.0")
    assert info.value.key == "gdb_reason_exists"
    assert_installed(dest)


def test_only_jp_and_plain_versions(tmp_path: pathlib.Path) -> None:
    package = make_xapk(tmp_path / "game.xapk")

    with pytest.raises(gdb.BuildError) as info:
        gdb.install_from_package(package, tmp_path / "gd", "16.0.0", cc="en")
    assert info.value.key == "gdb_reason_unsupported"

    for version in ("../16.0.0", "16", "16.0.0/..", "16.0.x"):
        with pytest.raises(gdb.BuildError) as info:
            gdb.install_from_package(package, tmp_path / "gd", version)
        assert info.value.key == "gdb_reason_bad_version"
    assert not (tmp_path / "gd").exists()
    assert gdb.is_supported("jp") and not gdb.is_supported("en")


def test_decrypt_list_round_trip() -> None:
    list_data, pack_data = make_pack(DATA_FILES)

    entries = gdb.decrypt_list(list_data, "DataLocal")

    assert [name for name, _, _ in entries] == list(DATA_FILES)
    for (name, offset, length), content in zip(entries, DATA_FILES.values()):
        assert gdb.decrypt_entry(pack_data, offset, length) == content, name


# ------------------------------------------------------------- downloads (mocked)


def test_download_with_sha256_mismatch_is_refused_and_cleaned_up(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_bytes = make_xapk(tmp_path / "game.xapk").read_bytes()
    wrong = hashlib.sha256(b"something else").hexdigest()
    network = FakeNetwork(
        {
            gdb.INDEX_URL: FakeResponse(
                content=index_json(
                    [index_entry("16.0.0", PACKAGE_URL, wrong, len(package_bytes))]
                )
            ),
            PACKAGE_URL: FakeResponse(content=package_bytes),
        }
    )
    monkeypatch.setattr(gdb.requests, "get", network.get)
    monkeypatch.delenv(gdb.PACKAGE_ENV, raising=False)
    game_data = tmp_path / "game_data"

    with pytest.raises(gdb.BuildError) as info:
        gdb.build_version("16.0.0", game_data)

    assert info.value.key == "gdb_reason_sha256"
    assert info.value.kwargs["expected"] == wrong[:12]
    assert not (game_data / "jp" / "16.0.0").exists()
    assert leftovers(game_data / "jp") == []  # the downloaded temp file is gone
    assert [call["url"] for call in network.calls] == [gdb.INDEX_URL, PACKAGE_URL]


def test_download_verifies_sha256_and_reports_progress(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_bytes = make_xapk(tmp_path / "game.xapk").read_bytes()
    sha256 = (
        hashlib.sha256(package_bytes).hexdigest().upper()
    )  # index case does not matter
    network = FakeNetwork(
        {
            gdb.INDEX_URL: FakeResponse(
                content=index_json(
                    [
                        index_entry("15.10.0", "https://download.invalid/old.xapk"),
                        index_entry("16.0.0", PACKAGE_URL, sha256, len(package_bytes)),
                    ]
                )
            ),
            PACKAGE_URL: FakeResponse(content=package_bytes),
        }
    )
    monkeypatch.setattr(gdb.requests, "get", network.get)
    monkeypatch.setattr(gdb, "CHUNK_SIZE", 97)
    monkeypatch.delenv(gdb.PACKAGE_ENV, raising=False)
    status = Recorder()

    dest = gdb.build_version("16.0.0", tmp_path / "game_data", status=status)

    assert_installed(dest)
    assert leftovers(dest.parent) == []
    keys = status.keys()
    assert keys[:2] == ["gdb_status_index", "gdb_status_download_start"]
    assert keys.count("gdb_status_download") == 11  # 0%, 10%, ..., 100%
    assert keys[-3:] == [
        "gdb_status_extract",
        "gdb_status_extracted",
        "gdb_status_extracted",
    ]
    index_call, package_call = network.calls
    assert index_call["headers"]["User-Agent"] == gdb.USER_AGENT
    assert not gdb.USER_AGENT.lower().startswith(("python", "urllib"))
    assert index_call["timeout"] == gdb.INDEX_TIMEOUT
    assert package_call["timeout"] == gdb.DOWNLOAD_TIMEOUT and package_call["stream"]
    assert package_call["headers"]["Accept-Encoding"] == "identity"
    # never hang when offline: short connect timeouts
    assert gdb.INDEX_TIMEOUT[0] <= 10 and gdb.DOWNLOAD_TIMEOUT[0] <= 10


@pytest.mark.parametrize(
    "route, key",
    [
        (requests.ConnectionError("offline"), "gdb_reason_network"),
        (requests.ConnectTimeout("slow"), "gdb_reason_network"),
        (FakeResponse(status_code=403), "gdb_reason_http"),
        (FakeResponse(content=b"<html>"), "gdb_reason_index"),
        (FakeResponse(content=index_json([])), "gdb_reason_index"),
    ],
)
def test_index_failures(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    route: FakeResponse | Exception,
    key: str,
) -> None:
    network = FakeNetwork({gdb.INDEX_URL: route})
    monkeypatch.setattr(gdb.requests, "get", network.get)
    monkeypatch.delenv(gdb.PACKAGE_ENV, raising=False)

    with pytest.raises(gdb.BuildError) as info:
        gdb.build_version("16.0.0", tmp_path / "game_data")

    assert info.value.key == key
    assert not (tmp_path / "game_data" / "jp" / "16.0.0").exists()


def test_version_not_offered(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        index_entry(v, f"https://download.invalid/{v}.xapk")
        for v in ("15.7.2", "15.10.0", "15.9.1")
    ]
    network = FakeNetwork({gdb.INDEX_URL: FakeResponse(content=index_json(entries))})
    monkeypatch.setattr(gdb.requests, "get", network.get)
    monkeypatch.delenv(gdb.PACKAGE_ENV, raising=False)

    with pytest.raises(gdb.BuildError) as info:
        gdb.build_version("16.0.0", tmp_path / "game_data")

    assert (info.value.key, info.value.kwargs) == (
        "gdb_reason_version_unavailable",
        {"version": "16.0.0", "newest": "15.10.0"},
    )
    assert leftovers(tmp_path / "game_data" / "jp") == []


def test_parse_index_skips_malformed_entries() -> None:
    payload = {
        "version_list": [
            "junk",
            {"version_name": "../../etc", "asset": {"url": "https://x.invalid/a"}},
            {"version_name": "16.0.0", "asset": {"url": "http://insecure.invalid/a"}},
            {"version_name": "16.0.0"},
            {
                "version_name": "15.10.0",
                "asset": {
                    "url": "https://x.invalid/b",
                    "file_sha256": "zz",
                    "size": "12",
                },
            },
            {
                "version_name": "15.9.0",
                "asset": {"url": "https://x.invalid/c", "size": 7},
            },
        ]
    }

    packages = gdb.parse_index(payload)

    assert packages == [
        gdb.PackageInfo("15.10.0", "https://x.invalid/b", None, 12),
        gdb.PackageInfo("15.9.0", "https://x.invalid/c", None, 7),
    ]
    assert gdb.available_versions(packages) == ["15.10.0", "15.9.0"]
    with pytest.raises(gdb.BuildError):
        gdb.parse_index({"version_list": "nope"})


def test_package_override_needs_no_network(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = make_xapk(tmp_path / "game.xapk")
    monkeypatch.setenv(gdb.PACKAGE_ENV, str(package))
    forbid_network(monkeypatch)
    status = Recorder()

    dest = gdb.build_version("16.0.0", tmp_path / "game_data", status=status)

    assert_installed(dest)
    assert status.calls[0] == ("gdb_status_package_file", {"path": str(package)})
    assert package.exists()  # the user's file is never deleted


# ------------------------------------------- (b) end to end with the real package


@pytest.mark.skipif(
    not (REAL_PACKAGE.is_file() and REAL_REFERENCE.is_dir()),
    reason="cached jp 16.0.0 package or reference game data not present",
)
def test_real_package_is_byte_identical_to_reference(tmp_path: pathlib.Path) -> None:
    dest = gdb.install_from_package(REAL_PACKAGE, tmp_path / "game_data", "16.0.0")

    built, reference = tree(dest), tree(REAL_REFERENCE)
    assert sorted(built) == sorted(reference)
    different = [
        name
        for name, path in built.items()
        if path.read_bytes() != reference[name].read_bytes()
    ]
    assert different == []
    assert len(built) > 10_000
    print(
        f"\nbyte-identical: {len(built)} files (incl. the marker) vs {REAL_REFERENCE}"
    )


# ----------------------------------------------- (d) the hook in GameDataGetter


JP = core.CountryCode(core.CountryCodeType.JP)
SAVE_VERSION = core.GameVersion.from_string("16.0.0")
BCDATA_BASE = "https://bcdata.invalid/"


def bcdata_metadata(*versions: str) -> dict[str, Any]:
    return {
        "base_url": BCDATA_BASE,
        "versions": {"jp": {v: f"jp/{v}.tar.xz" for v in versions}},
    }


def bcdata_archive(content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        info = tarfile.TarInfo("DataLocal/unitbuy.csv")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class HookHarness:
    """Mocks the dialogs, BCData and the network around GameDataGetter."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any] | None
    ):
        self.answers: list[bool | None] = []
        self.prompts: list[tuple[str, dict[str, Any]]] = []
        self.printed: list[tuple[str, dict[str, Any]]] = []
        self.metadata_calls = 0
        self.bcdata_downloads: list[str] = []
        self.network = FakeNetwork({gdb.INDEX_URL: requests.ConnectionError("offline")})
        archive = bcdata_archive(b"old unitbuy")

        def yes_no_key(dialog: str, escape: bool = False, **kwargs: Any) -> bool | None:
            self.prompts.append((dialog, kwargs))
            return self.answers.pop(0)

        def color_print_key(
            key: str, end: str = "\n", escape: bool = True, **kwargs: Any
        ) -> None:
            self.printed.append((key, kwargs))

        def get_metadata(show_alt: bool = True) -> dict[str, Any] | None:
            self.metadata_calls += 1
            return metadata

        def bcdata_get(handler: core.RequestHandler, *args: Any, **kwargs: Any) -> Any:
            self.bcdata_downloads.append(handler.url)
            if handler.url == BCDATA_BASE + "jp/15.7.2.tar.xz":
                return types.SimpleNamespace(content=archive)
            return None

        monkeypatch.setattr(game_data_getter, "_app_build_offered", set())
        monkeypatch.setattr(dialog_creator, "yes_no_key", yes_no_key)
        monkeypatch.setattr(color, "color_print_key", color_print_key)
        monkeypatch.setattr(GameDataGetter, "get_metadata", staticmethod(get_metadata))
        monkeypatch.setattr(core.RequestHandler, "get", bcdata_get)
        monkeypatch.setattr(gdb.requests, "get", self.network.get)

    def new_getter(self) -> GameDataGetter:
        # the feature handler drops the getter after every feature; do the same
        core.core_data.game_data_getter = None
        return core.core_data.get_game_data_getter(cc=JP, gv=SAVE_VERSION)

    def printed_keys(self) -> list[str]:
        return [key for key, _ in self.printed]


@pytest.mark.parametrize("answer", [False, None])
def test_declined_offer_keeps_the_fallback_and_is_not_repeated(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch, answer: bool | None
) -> None:
    harness = HookHarness(monkeypatch, bcdata_metadata("15.6.0", "15.7.2"))
    harness.answers = [answer]

    getter = harness.new_getter()

    assert harness.prompts == [("gdb_build_offer", {"version": "16.0.0"})]
    assert "gdb_build_declined" in harness.printed_keys()
    assert (getter.version, getter.filepath) == ("15.7.2", "jp/15.7.2.tar.xz")
    data = getter.download("DataLocal", "unitbuy.csv")
    assert (
        data is not None and data.data == b"old unitbuy"
    )  # BCData fallback still works
    assert harness.network.calls == []

    again = harness.new_getter()

    assert len(harness.prompts) == 1
    assert again.version == "15.7.2"
    assert not (editor_env / "game_data" / "jp" / "16.0.0").exists()


def test_failed_build_prints_reason_and_falls_back_once(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, bcdata_metadata("15.7.2"))
    harness.answers = [True]

    getter = harness.new_getter()

    assert len(harness.prompts) == 1
    assert ("gdb_build_failed", {"reason": "gdb_reason_network"}) in harness.printed
    assert [call["url"] for call in harness.network.calls] == [gdb.INDEX_URL]
    assert harness.network.calls[0]["timeout"] == gdb.INDEX_TIMEOUT
    assert getter.version == "15.7.2"
    data = getter.download("DataLocal", "unitbuy.csv")
    assert data is not None and data.data == b"old unitbuy"

    again = harness.new_getter()

    assert len(harness.prompts) == 1  # not asked again
    assert len(harness.network.calls) == 1  # and not retried
    assert again.version == "15.7.2"
    assert leftovers(editor_env / "game_data" / "jp") == []


def test_unreachable_bcdata_still_offers_and_keeps_old_behaviour(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, None)
    harness.answers = [False]

    getter = harness.new_getter()

    assert len(harness.prompts) == 1
    assert getter.version is None and getter.url is None  # as before this change
    assert getter.download("DataLocal", "unitbuy.csv", display_text=False) is None


def test_accepted_offer_builds_the_exact_version(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, bcdata_metadata("15.7.2"))
    harness.answers = [True]
    package = make_xapk(editor_env / "game.xapk")
    monkeypatch.setenv(gdb.PACKAGE_ENV, str(package))

    getter = harness.new_getter()

    assert (getter.version, getter.url, getter.filepath) == ("16.0.0", None, None)
    assert harness.printed_keys()[0] == "gdb_build_start"
    assert "gdb_build_done" in harness.printed_keys()
    data = getter.download("DataLocal", "unitbuy.csv")
    assert data is not None and data.data == DATA_FILES["unitbuy.csv"]
    assert harness.bcdata_downloads == []
    assert harness.network.calls == []

    calls_before = harness.metadata_calls
    again = harness.new_getter()

    assert again.version == "16.0.0"
    assert harness.metadata_calls == calls_before  # exact local match: BCData not asked
    assert len(harness.prompts) == 1


def test_no_offer_when_bcdata_has_the_exact_version(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, bcdata_metadata("15.7.2", "16.0.0"))

    getter = harness.new_getter()

    assert harness.prompts == []
    assert (getter.version, getter.filepath) == ("16.0.0", "jp/16.0.0.tar.xz")


def test_no_offer_for_other_regions(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, None)
    core.core_data.game_data_getter = None

    getter = core.core_data.get_game_data_getter(
        cc=core.CountryCode(core.CountryCodeType.EN), gv=SAVE_VERSION
    )

    assert harness.prompts == []
    assert getter.version is None


def test_manage_game_data_builds_the_newest_version_by_default(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, None)
    package_bytes = make_xapk(editor_env / "game.xapk", version="16.0.1").read_bytes()
    sha256 = hashlib.sha256(package_bytes).hexdigest()
    newest_url = "https://download.invalid/16.0.1.xapk"
    harness.network.routes = {
        gdb.INDEX_URL: FakeResponse(
            content=index_json(
                [
                    index_entry("16.0.0", PACKAGE_URL),
                    index_entry("16.0.1", newest_url, sha256, len(package_bytes)),
                ]
            )
        ),
        newest_url: FakeResponse(content=package_bytes),
    }
    questions: list[tuple[str, dict[str, Any]]] = []

    def str_input_key(dialog: str, escape: bool = True, **kwargs: Any) -> str | None:
        questions.append((dialog, kwargs))
        return ""

    monkeypatch.setattr(dialog_creator, "str_input_key", str_input_key)

    built = GameDataGetter.build_from_app_menu(JP)

    assert built == core.GameVersion.from_string("16.0.1")
    assert questions == [("gdb_version_q", {"default": "16.0.1"})]
    assert ("gdb_available_versions", {"versions": "16.0.1, 16.0.0"}) in harness.printed
    assert_installed(editor_env / "game_data" / "jp" / "16.0.1")
    assert [call["url"] for call in harness.network.calls] == [
        gdb.INDEX_URL,
        newest_url,
    ]

    again = GameDataGetter.build_from_app_menu(JP)

    assert again == core.GameVersion.from_string("16.0.1")
    assert ("gdb_already_present", {"version": "16.0.1"}) in harness.printed
    assert [call["url"] for call in harness.network.calls].count(newest_url) == 1


def test_manage_game_data_refuses_other_regions(
    editor_env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = HookHarness(monkeypatch, None)

    assert GameDataGetter.build_from_app_menu(core.CountryCode("kr")) is None
    assert harness.printed[-1][0] == "gdb_build_failed"
    assert harness.network.calls == []


# ------------------------------------------------------------------- locale keys


def _source_keys(prefix: str) -> set[str]:
    pattern = re.compile(rf"[\"']({prefix}[a-z0-9_]+)[\"']")
    keys: set[str] = set()
    for path in (REPO / "src" / "bcsfe").rglob("*.py"):
        keys |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return keys


def test_game_data_locale_keys(editor_env: pathlib.Path) -> None:
    # copy the packaged files/ into the temp data dir like migrate() does, then use
    # the editor's own parser on them
    packaged = resources.files("bcsfe").joinpath("files")
    bcsfe.copy_to_data_dir(packaged, packaged)

    en = core.locale_handler.PropertySet("en", "fork/game_data").properties
    ja = core.locale_handler.PropertySet("ja", "fork/game_data").properties

    used = _source_keys("gdb_")
    assert used and used <= set(en), sorted(used - set(en))
    assert set(en) == set(ja)
    for key in en:
        en_args = set(core.LocalManager.get_kwarg_keys(en[key][0]))
        ja_args = set(core.LocalManager.get_kwarg_keys(ja[key][0]))
        assert en_args == ja_args, key
    # the key namespace is flat and global: no clash with the existing English keys
    others: set[str] = set()
    for path in (editor_env / "data" / "locales" / "en").rglob("*.properties"):
        name = path.relative_to(editor_env / "data" / "locales" / "en").as_posix()[:-11]
        if name != "fork/game_data":
            others |= set(core.locale_handler.PropertySet("en", name).properties)
    assert not set(en) & others
    assert (
        "{{y/n}}" in en["gdb_build_offer"][0] and "{{y/n}}" in ja["gdb_build_offer"][0]
    )
