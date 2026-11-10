"""Create game data for the editor straight from the official Android package (jp).

The editor reads game tables from ``<game_data>/<cc>/<version>/{DataLocal,resLocal}/``
plus an empty ``downloaded`` marker. Those normally come from the BCData repo, which
lags behind the JP app; when it has no entry for the save's exact version the editor
falls back to an older one, so newly added cats show up as bare IDs. This module builds
the same directory BCData would publish, from the package itself:

    package index -> XAPK (zip of split APKs) or APK -> assets/{DataLocal,resLocal}.{list,pack}
    -> .list: AES-128-ECB, .pack entries: AES-128-CBC (JP key/iv), PKCS7 padding -> files

Formats and keys follow fieryhenry's tbctk (tbctk-asset/src/{list,crypto}.rs), which is
what BCData itself uses. Only jp is supported: the other regions' keys are not verified.

Set the environment variable ``BCSFE_GAME_PACKAGE`` to the path of an already downloaded
``.xapk``/``.apk`` to build from that file instead of contacting the package index (tests
and offline use). An XAPK's ``manifest.json`` must then name the requested version.

The module never prints or prompts. Failures raise :class:`BuildError`, whose ``key`` and
``kwargs`` name a locale entry (``gdb_reason_*``); progress goes to an optional
``status(key, **kwargs)`` callback with ``gdb_status_*`` keys.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import tempfile
import uuid
import zipfile
from typing import Any, Protocol, cast

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SUPPORTED_CC = "jp"
PACKAGE_NAME = "jp.co.ponos.battlecats"
PACKS = ("DataLocal", "resLocal")
MARKER = "downloaded"
PACKAGE_ENV = "BCSFE_GAME_PACKAGE"

INDEX_URL = (
    f"https://tapi.pureapk.com/v3/get_app_his_version?package_name={PACKAGE_NAME}"
)
# The index answers 403 to default library User-Agents, so name the client explicitly.
USER_AGENT = "bcsfe-game-data/1.0"
INDEX_HEADERS = {
    "User-Agent": USER_AGENT,
    "Ual-Access-Businessid": "projecta",
    "Ual-Access-ProjectA": json.dumps({"device_info": {"os_ver": "33"}}),
}

# (connect, read) timeouts in seconds: fail fast when offline, tolerate a slow mirror.
INDEX_TIMEOUT = (6.0, 20.0)
DOWNLOAD_TIMEOUT = (6.0, 60.0)
CHUNK_SIZE = 1 << 20
# Refuse absurdly large .list/.pack members (the real ones are a few MB).
MAX_ASSET_SIZE = 512 << 20

# Asset keys published in tbctk. .list files: AES-128-ECB. JP .pack entries: AES-128-CBC.
LIST_KEY = b"b484857901742afc"
PACK_KEY = bytes.fromhex("d754868de89d717fa9e7b06da45ae9e3")
PACK_IV = bytes.fromhex("40b2131a9f388ad4e5002a98118f6128")

_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
# Errors from reading a damaged or unexpected archive.
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    EOFError,
    NotImplementedError,
    RuntimeError,
    ValueError,
)


class StatusCallback(Protocol):
    """Receives progress as a locale key (``gdb_status_*``) plus its placeholders."""

    def __call__(self, key: str, /, **kwargs: Any) -> None: ...


class BuildError(Exception):
    """A build step failed; ``key``/``kwargs`` name the locale entry with the reason."""

    def __init__(self, key: str, **kwargs: Any) -> None:
        super().__init__(key, kwargs)
        self.key = key
        self.kwargs = kwargs

    def __str__(self) -> str:
        details = ", ".join(f"{name}={value}" for name, value in self.kwargs.items())
        return f"{self.key} ({details})" if details else self.key


@dataclasses.dataclass(frozen=True)
class PackageInfo:
    """One downloadable package as listed by the index."""

    version: str
    url: str
    sha256: str | None
    size: int | None


def is_supported(cc: str) -> bool:
    """Whether game data for region ``cc`` (e.g. ``"jp"``) can be built from the app."""
    return cc == SUPPORTED_CC


def is_valid_version(version: str) -> bool:
    """Whether ``version`` looks like a game version (``16.0.0``); safe as a dir name."""
    return _VERSION_RE.fullmatch(version) is not None


def version_key(version: str) -> tuple[int, ...]:
    """Sort key ordering versions numerically (``15.10.0`` after ``15.7.2``)."""
    return tuple(int(part) for part in version.split("."))


def get_package_override() -> pathlib.Path | None:
    """The package file named by ``BCSFE_GAME_PACKAGE``, if that variable is set."""
    value = os.environ.get(PACKAGE_ENV, "").strip()
    return pathlib.Path(value).expanduser() if value else None


def fetch_index() -> list[PackageInfo]:
    """Ask the package index which versions of the JP app it can serve."""
    try:
        with requests.get(
            INDEX_URL, headers=INDEX_HEADERS, timeout=INDEX_TIMEOUT
        ) as response:
            status_code = response.status_code
            body = response.content
    except requests.RequestException as e:
        raise BuildError("gdb_reason_network", error=type(e).__name__) from e
    if status_code != 200:
        raise BuildError("gdb_reason_http", status=status_code)
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise BuildError("gdb_reason_index") from e
    return parse_index(payload)


def parse_index(payload: Any) -> list[PackageInfo]:
    """Turn the index's JSON into package entries, skipping malformed ones."""
    document = _as_dict(payload)
    entries = document.get("version_list") if document is not None else None
    if not isinstance(entries, list):
        raise BuildError("gdb_reason_index")
    packages: list[PackageInfo] = []
    for raw_entry in cast("list[Any]", entries):
        entry = _as_dict(raw_entry)
        asset = _as_dict(entry.get("asset")) if entry is not None else None
        if entry is None or asset is None:
            continue
        version = entry.get("version_name")
        url = asset.get("url")
        if not isinstance(version, str) or not is_valid_version(version):
            continue
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        sha256 = asset.get("file_sha256")
        size = asset.get("size")
        packages.append(
            PackageInfo(
                version=version,
                url=url,
                sha256=(
                    sha256.lower()
                    if isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256)
                    else None
                ),
                size=_positive_int(size),
            )
        )
    if not packages:
        raise BuildError("gdb_reason_index")
    return packages


def available_versions(packages: list[PackageInfo]) -> list[str]:
    """Distinct versions in ``packages``, newest first."""
    return sorted({p.version for p in packages}, key=version_key, reverse=True)


def find_package(packages: list[PackageInfo], version: str) -> PackageInfo | None:
    """The first package for exactly ``version``, if the index has one."""
    for package in packages:
        if package.version == version:
            return package
    return None


def download_package(
    package: PackageInfo,
    target: str | os.PathLike[str],
    status: StatusCallback | None = None,
) -> None:
    """Download ``package`` into ``target``, checking its sha256 when the index gave one.

    ``target`` is overwritten. The caller owns it and removes it afterwards, also when
    this raises.
    """
    target_path = pathlib.Path(target)
    digest = hashlib.sha256()
    done = 0
    shown = -1
    try:
        with requests.get(
            package.url,
            # the file as published (its sha256 is in the index), not re-encoded
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            timeout=DOWNLOAD_TIMEOUT,
            stream=True,
        ) as response:
            if response.status_code != 200:
                raise BuildError("gdb_reason_http", status=response.status_code)
            total = package.size or _positive_int(
                response.headers.get("Content-Length")
            )
            total_mb = _mb(total) if total else "?"
            if status is not None:
                status(
                    "gdb_status_download_start", version=package.version, size=total_mb
                )
            with target_path.open("wb") as fh:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    step = done * 10 // total if total else done // (16 << 20)
                    if status is not None and step != shown:
                        shown = step
                        status("gdb_status_download", done=_mb(done), total=total_mb)
    except requests.RequestException as e:
        raise BuildError("gdb_reason_network", error=type(e).__name__) from e
    except OSError as e:
        raise BuildError(
            "gdb_reason_write", path=str(target_path), error=_describe_os_error(e)
        ) from e
    got = digest.hexdigest()
    if package.sha256 is not None and got != package.sha256:
        raise BuildError(
            "gdb_reason_sha256", expected=package.sha256[:12], got=got[:12]
        )


def read_manifest(package: str | os.PathLike[str]) -> dict[str, Any] | None:
    """An XAPK's ``manifest.json``, or None for a plain APK (which has none)."""
    try:
        with zipfile.ZipFile(package) as archive:
            if "manifest.json" not in archive.namelist():
                return None
            raw = archive.read("manifest.json")
    except _ZIP_ERRORS as e:
        raise BuildError("gdb_reason_package", error=type(e).__name__) from e
    try:
        manifest = json.loads(raw)
    except ValueError as e:
        raise BuildError("gdb_reason_package", error="manifest.json") from e
    return _as_dict(manifest)


def package_version(package: str | os.PathLike[str]) -> str | None:
    """The app version an XAPK declares, or None when it does not say."""
    manifest = read_manifest(package)
    version = manifest.get("version_name") if manifest is not None else None
    return version if isinstance(version, str) else None


def check_package_identity(package: str | os.PathLike[str], version: str) -> None:
    """Refuse an XAPK whose manifest names another app or another version."""
    manifest = read_manifest(package)
    if manifest is None:
        return
    name = manifest.get("package_name")
    if isinstance(name, str) and name != PACKAGE_NAME:
        raise BuildError("gdb_reason_wrong_package", found=name)
    found = manifest.get("version_name")
    if isinstance(found, str) and found != version:
        raise BuildError("gdb_reason_version_mismatch", found=found, version=version)


def find_packs(package: str | os.PathLike[str]) -> dict[str, tuple[bytes, bytes]]:
    """Return ``{pack name: (encrypted .list, .pack data)}`` for the packs in PACKS.

    An XAPK is a zip of split APKs and the packs sit in ``assets/`` of one of them; a
    plain APK has ``assets/`` at the top level, so the outer archive is searched too.
    """
    found: dict[str, tuple[bytes, bytes]] = {}

    def scan(archive: zipfile.ZipFile) -> None:
        names = set(archive.namelist())
        for pack in PACKS:
            list_name, pack_name = f"assets/{pack}.list", f"assets/{pack}.pack"
            if pack not in found and list_name in names and pack_name in names:
                found[pack] = (
                    _read_member(archive, list_name),
                    _read_member(archive, pack_name),
                )

    try:
        with zipfile.ZipFile(package) as outer:
            scan(outer)
            for name in outer.namelist():
                if len(found) == len(PACKS):
                    break
                if name.lower().endswith(".apk"):
                    # read through the outer zip instead of loading the APK into memory
                    with outer.open(name) as stream, zipfile.ZipFile(stream) as inner:
                        scan(inner)
    except _ZIP_ERRORS as e:
        raise BuildError("gdb_reason_package", error=type(e).__name__) from e

    missing = [pack for pack in PACKS if pack not in found]
    if missing:
        raise BuildError("gdb_reason_packs_missing", packs=", ".join(missing))
    return found


def decrypt_list(data: bytes, pack: str = "") -> list[tuple[str, int, int]]:
    """Decrypt a ``.list`` file into ``(file name, offset, length)`` entries."""
    try:
        text = _decrypt(LIST_KEY, None, data).decode("utf-8")
    except ValueError as e:  # not whole blocks, bad padding or not UTF-8
        raise BuildError(
            "gdb_reason_bad_list", pack=pack, error=type(e).__name__
        ) from e

    declared: int | None = None
    entries: list[tuple[str, int, int]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(",")
        if (
            len(fields) == 1
            and fields[0].isdecimal()
            and declared is None
            and not entries
        ):
            declared = int(fields[0])
        elif len(fields) == 3 and fields[1].isdecimal() and fields[2].isdecimal():
            entries.append((fields[0], int(fields[1]), int(fields[2])))
        else:
            raise BuildError("gdb_reason_bad_list", pack=pack, error=repr(line[:40]))
    if declared is not None and declared != len(entries):
        raise BuildError(
            "gdb_reason_bad_list",
            pack=pack,
            error=f"{declared} files declared, {len(entries)} listed",
        )
    return entries


def decrypt_entry(pack_data: bytes, offset: int, length: int) -> bytes:
    """Decrypt one file of a JP ``.pack``. Raises ValueError for a bad entry."""
    if offset < 0 or length < 0 or offset + length > len(pack_data) or length % 16:
        raise ValueError(
            f"bad entry (offset {offset}, length {length}, pack {len(pack_data)})"
        )
    return _decrypt(PACK_KEY, PACK_IV, memoryview(pack_data)[offset : offset + length])


def extract_packs(
    package: str | os.PathLike[str],
    outdir: str | os.PathLike[str],
    status: StatusCallback | None = None,
) -> dict[str, int]:
    """Decrypt the packs of ``package`` into ``outdir/<pack>/``; return file counts.

    Every file name of a list is checked before anything of that pack is written.
    """
    out = pathlib.Path(outdir)
    packs = find_packs(package)
    if status is not None:
        status("gdb_status_extract")
    counts: dict[str, int] = {}
    for pack in PACKS:
        list_data, pack_data = packs[pack]
        entries = decrypt_list(list_data, pack)
        pack_dir = out / pack
        targets = [_safe_target(pack_dir, name, pack) for name, _, _ in entries]
        pack_dir.mkdir(parents=True)
        for target, (name, offset, length) in zip(targets, entries):
            try:
                content = decrypt_entry(pack_data, offset, length)
            except ValueError as e:
                raise BuildError("gdb_reason_bad_entry", pack=pack, name=name) from e
            target.write_bytes(content)
        counts[pack] = len(entries)
        if status is not None:
            status("gdb_status_extracted", pack=pack, files=len(entries))
    return counts


def install_from_package(
    package: str | os.PathLike[str],
    game_data_dir: str | os.PathLike[str],
    version: str,
    cc: str = SUPPORTED_CC,
    status: StatusCallback | None = None,
) -> pathlib.Path:
    """Create ``<game_data_dir>/<cc>/<version>`` from an already downloaded package.

    Files are decrypted into a staging directory next to the destination, which is then
    renamed into place in one step; the ``downloaded`` marker is written last, so the
    editor never picks up a half-written version. A destination without the marker
    (left by an interrupted download) is replaced, a complete one is refused. Nothing
    is written outside ``<game_data_dir>/<cc>/`` and temporary files are always removed.
    """
    _check_target(version, cc)
    package_path = pathlib.Path(package)
    if not package_path.is_file():
        raise BuildError("gdb_reason_missing_file", path=str(package_path))
    check_package_identity(package_path, version)

    region_dir = pathlib.Path(game_data_dir) / cc
    dest = region_dir / version
    if (dest / MARKER).exists():
        raise BuildError("gdb_reason_exists", version=version)

    staging: pathlib.Path | None = None
    old: pathlib.Path | None = None
    try:
        region_dir.mkdir(parents=True, exist_ok=True)
        staging = pathlib.Path(
            tempfile.mkdtemp(prefix=f".gdb-{version}-", dir=region_dir)
        )
        extract_packs(package_path, staging, status)
        if dest.exists() or dest.is_symlink():
            old = region_dir / f".gdb-{version}-old-{uuid.uuid4().hex[:8]}"
            os.replace(dest, old)
        os.replace(staging, dest)
        staging = None
        (dest / MARKER).write_bytes(b"")
    except OSError as e:
        raise BuildError(
            "gdb_reason_write", path=str(region_dir), error=_describe_os_error(e)
        ) from e
    finally:
        if staging is not None:
            _remove(staging)
        if old is not None:
            _remove(old)
    return dest


def build_version(
    version: str,
    game_data_dir: str | os.PathLike[str],
    cc: str = SUPPORTED_CC,
    status: StatusCallback | None = None,
    packages: list[PackageInfo] | None = None,
) -> pathlib.Path:
    """Create game data for ``version`` from the official app and return its directory.

    Uses the file named by ``BCSFE_GAME_PACKAGE`` when that is set. Otherwise looks the
    version up in the package index (or in ``packages``, fetched earlier by the caller),
    downloads it to a temporary file next to the destination and removes that file
    afterwards, whatever happens.
    """
    _check_target(version, cc)
    region_dir = pathlib.Path(game_data_dir) / cc
    if (region_dir / version / MARKER).exists():
        raise BuildError("gdb_reason_exists", version=version)

    override = get_package_override()
    if override is not None:
        if status is not None:
            status("gdb_status_package_file", path=str(override))
        return install_from_package(override, game_data_dir, version, cc, status)

    if packages is None:
        if status is not None:
            status("gdb_status_index")
        packages = fetch_index()
    package = find_package(packages, version)
    if package is None:
        versions = available_versions(packages)
        raise BuildError(
            "gdb_reason_version_unavailable",
            version=version,
            newest=versions[0] if versions else "-",
        )

    try:
        region_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".gdb-{version}-", suffix=".part", dir=region_dir
        )
        os.close(fd)
    except OSError as e:
        raise BuildError(
            "gdb_reason_write", path=str(region_dir), error=_describe_os_error(e)
        ) from e
    temp = pathlib.Path(temp_name)
    try:
        download_package(package, temp, status)
        return install_from_package(temp, game_data_dir, version, cc, status)
    finally:
        _remove(temp)


def _check_target(version: str, cc: str) -> None:
    if not is_supported(cc):
        raise BuildError("gdb_reason_unsupported", cc=cc)
    if not is_valid_version(version):
        raise BuildError("gdb_reason_bad_version", version=version)


def _safe_target(pack_dir: pathlib.Path, name: str, pack: str) -> pathlib.Path:
    # names come from the package; never let one escape the pack directory
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise BuildError("gdb_reason_bad_name", pack=pack, name=repr(name))
    target = pack_dir / name
    if target.parent != pack_dir:  # e.g. a drive-relative name on Windows
        raise BuildError("gdb_reason_bad_name", pack=pack, name=repr(name))
    return target


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_ASSET_SIZE:
        raise BuildError("gdb_reason_package", error=f"{name}: {info.file_size} bytes")
    return archive.read(info)


def _decrypt(key: bytes, iv: bytes | None, data: bytes | memoryview) -> bytes:
    """AES-128 decrypt (CBC when ``iv`` is given, else ECB) and strip PKCS7 padding."""
    mode = modes.ECB() if iv is None else modes.CBC(iv)
    decryptor = Cipher(algorithms.AES(key), mode).decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(plain) + unpadder.finalize()


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdecimal():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _mb(size: int) -> int:
    return size >> 20


def _describe_os_error(error: OSError) -> str:
    return error.strerror or type(error).__name__


def _remove(path: pathlib.Path) -> None:
    """Best-effort removal of a temporary file or directory."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
