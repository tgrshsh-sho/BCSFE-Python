"""Consistency checks for the Japanese (ja) locale against the English (en) reference.

The .properties files are parsed directly (the editor itself is not started), so
these tests also cover files that other work packages add under en/fork and ja/fork.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

LOCALES_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "bcsfe" / "files" / "locales"
)
EN_DIR = LOCALES_DIR / "en"
JA_DIR = LOCALES_DIR / "ja"

# Keys whose localized value is compared with what the user types, so they must
# stay easy to type on any keyboard.
REQUIRED_ASCII_KEYS = ("yes_key", "quit_key")
FUNCTIONAL_TOKEN_KEYS = ("yes_key", "quit_key", "all", "max", "infinity")

REF_RE = re.compile(r"\{\{([^{}]+)\}\}")
PLACEHOLDER_RE = re.compile(r"(?<!\\)\{([^{}]*)\}")
ESCAPE_RE = re.compile(r"\\[<>/]")
TAG_RE = re.compile(r"<([^<>]*)>")
CONDITION_RE = re.compile(r"\$\(\s*([^:()$]+?)\s*:")


class Entry:
    def __init__(self, key: str, value: str, multiline: bool, line_no: int):
        self.key = key
        self.value = value
        self.multiline = multiline
        self.line_no = line_no


def parse_entries(text: str) -> list[Entry]:
    """Parse a .properties file the way it is meant to be read.

    Multi-line values start with a line ending in ``=`` followed by lines
    prefixed with ``>``. Unlike the editor's parser, a key written directly after
    a multi-line block is not lost.
    """
    entries: list[Entry] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("#") or line.startswith(">"):
            i += 1
            continue
        key, _, value = line.partition("=")
        if line.strip().endswith("="):
            start = i + 1
            block: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i][1:])
                i += 1
            entries.append(Entry(key, "\n".join(block), True, start))
            continue
        entries.append(Entry(key, value, False, i + 1))
        i += 1
    return entries


def parse_like_editor(text: str) -> dict[str, str]:
    """Replica of ``bcsfe.core.locale_handler.PropertySet.parse``.

    Used to make sure the editor loads every ja key with the intended value.
    """
    lines = text.splitlines()
    properties: dict[str, str] = {}
    i = 0
    in_multi_line = False
    multi_line_text = ""
    multi_line_key = ""
    while i < len(lines):
        line = lines[i]
        finish_multiline = False
        if (in_multi_line and not line.startswith(">")) or (
            in_multi_line and i == len(lines) - 1
        ):
            in_multi_line = False
            finish_multiline = True
            if multi_line_key in properties:
                raise KeyError(f"Key {multi_line_key} already exists in property file")
            if line.startswith(">"):
                multi_line_text += line[1:]
            else:
                multi_line_text = multi_line_text[:-1]
            properties[multi_line_key] = multi_line_text
            multi_line_text = ""
            multi_line_key = ""
        if line.startswith("#") or not line:
            i += 1
            continue
        if line.startswith(">") and in_multi_line:
            multi_line_text += line[1:] + "\n"
        parts = line.split("=")
        if line.strip().endswith("="):
            in_multi_line = True
            multi_line_key = parts[0]
        if not in_multi_line and not finish_multiline:
            key = parts[0]
            if key in properties:
                raise KeyError(f"Key {key} already exists in property file")
            properties[key] = "=".join(parts[1:])
        i += 1
    return properties


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def relative_files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(p.relative_to(base) for p in base.rglob("*.properties"))


def entry_map(path: Path) -> dict[str, Entry]:
    return {entry.key: entry for entry in parse_entries(read(path))}


def refs(value: str) -> Counter[str]:
    return Counter(REF_RE.findall(value))


def placeholders(value: str) -> Counter[str]:
    return Counter(PLACEHOLDER_RE.findall(REF_RE.sub("", value)))


def tags(value: str) -> tuple[Counter[str], Counter[str]]:
    """Return (opening tags, closing tags) found in a value, ignoring escapes."""
    opening: Counter[str] = Counter()
    closing: Counter[str] = Counter()
    for tag in TAG_RE.findall(ESCAPE_RE.sub("", value)):
        if tag.startswith("/"):
            closing[tag] += 1
        else:
            opening[tag] += 1
    return opening, closing


def conditions(value: str) -> Counter[str]:
    return Counter(CONDITION_RE.findall(value))


EN_FILES = relative_files(EN_DIR)
JA_FILES = relative_files(JA_DIR)


def test_locale_directories_exist():
    assert EN_FILES, f"no en locale files found under {EN_DIR}"
    assert JA_FILES, f"no ja locale files found under {JA_DIR}"


def test_ja_metadata():
    metadata = json.loads(read(JA_DIR / "metadata.json"))
    assert metadata.get("name") == "日本語"
    authors = metadata.get("authors")
    assert isinstance(authors, list) and authors and all(authors)


def test_same_files_as_en():
    missing = [str(p) for p in EN_FILES if p not in JA_FILES]
    extra = [str(p) for p in JA_FILES if p not in EN_FILES]
    assert not missing, f"en files without a ja counterpart: {missing}"
    assert not extra, f"ja files without an en counterpart: {extra}"


@pytest.mark.parametrize("rel_path", EN_FILES, ids=str)
def test_same_keys_as_en(rel_path: Path):
    ja_path = JA_DIR / rel_path
    if not ja_path.exists():
        pytest.fail(f"missing ja file: {rel_path}")
    en_keys = set(entry_map(EN_DIR / rel_path))
    ja_keys = set(entry_map(ja_path))
    assert not sorted(en_keys - ja_keys), (
        f"{rel_path}: keys missing in ja: {sorted(en_keys - ja_keys)}"
    )
    assert not sorted(ja_keys - en_keys), (
        f"{rel_path}: keys only in ja: {sorted(ja_keys - en_keys)}"
    )


@pytest.mark.parametrize("rel_path", JA_FILES, ids=str)
def test_ja_file_loads_as_intended(rel_path: Path):
    """The editor's own parser must load every ja key with the intended value.

    This catches duplicate keys inside a file, keys swallowed after a multi-line
    block, and values that accidentally end with ``=``.
    """
    text = read(JA_DIR / rel_path)
    entries = parse_entries(text)
    keys = [entry.key for entry in entries]
    duplicates = sorted(k for k, n in Counter(keys).items() if n > 1)
    assert not duplicates, f"{rel_path}: keys defined twice: {duplicates}"
    loaded = parse_like_editor(text)
    intended = {entry.key: entry.value for entry in entries}
    assert loaded == intended, (
        f"{rel_path}: the editor would load different keys/values: "
        f"dropped={sorted(set(intended) - set(loaded))} "
        f"unexpected={sorted(set(loaded) - set(intended))} "
        f"changed={sorted(k for k in intended if k in loaded and loaded[k] != intended[k])}"
    )


def test_no_key_defined_twice_across_ja_files():
    seen: dict[str, Path] = {}
    duplicates: list[str] = []
    for rel_path in JA_FILES:
        for entry in parse_entries(read(JA_DIR / rel_path)):
            if entry.key in seen and seen[entry.key] != rel_path:
                duplicates.append(f"{entry.key} ({seen[entry.key]}, {rel_path})")
            seen.setdefault(entry.key, rel_path)
    assert not duplicates, f"keys defined in more than one ja file: {duplicates}"


@pytest.mark.parametrize("rel_path", EN_FILES, ids=str)
def test_values_keep_en_structure(rel_path: Path):
    """Per key: same placeholders, references, colour tags, conditions and line style."""
    ja_path = JA_DIR / rel_path
    if not ja_path.exists():
        pytest.fail(f"missing ja file: {rel_path}")
    en_entries = entry_map(EN_DIR / rel_path)
    ja_entries = entry_map(ja_path)
    problems: list[str] = []
    for key, en in en_entries.items():
        ja = ja_entries.get(key)
        if ja is None:
            continue  # reported by test_same_keys_as_en
        where = f"{rel_path}:{ja.line_no} {key}"
        if placeholders(en.value) != placeholders(ja.value):
            problems.append(
                f"{where}: placeholders {dict(placeholders(ja.value))} "
                f"!= en {dict(placeholders(en.value))}"
            )
        if refs(en.value) != refs(ja.value):
            problems.append(
                f"{where}: references {dict(refs(ja.value))} != en {dict(refs(en.value))}"
            )
        en_open, en_close = tags(en.value)
        ja_open, ja_close = tags(ja.value)
        if en_open != ja_open:
            problems.append(
                f"{where}: opening tags {dict(ja_open)} != en {dict(en_open)}"
            )
        if en_close != ja_close:
            problems.append(
                f"{where}: closing tags {dict(ja_close)} != en {dict(en_close)}"
            )
        if sum(en_open.values()) - sum(en_close.values()) != sum(
            ja_open.values()
        ) - sum(ja_close.values()):
            problems.append(f"{where}: tag balance differs from en")
        if conditions(en.value) != conditions(ja.value):
            problems.append(
                f"{where}: $(...) conditions {dict(conditions(ja.value))} "
                f"!= en {dict(conditions(en.value))}"
            )
        if en.multiline != ja.multiline:
            problems.append(f"{where}: multi-line style differs from en")
    assert not problems, "\n".join(problems)


def _all_ja_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for rel_path in JA_FILES:
        for entry in parse_entries(read(JA_DIR / rel_path)):
            values.setdefault(entry.key, entry.value)
    return values


@pytest.mark.parametrize("key", REQUIRED_ASCII_KEYS)
def test_input_keys_are_ascii(key: str):
    values = _all_ja_values()
    assert key in values, f"{key} is not defined in ja"
    value = values[key]
    assert value.strip(), f"{key} is empty"
    assert value.isascii(), f"{key}={value!r} must be ASCII so it can be typed"


@pytest.mark.parametrize("key", FUNCTIONAL_TOKEN_KEYS)
def test_functional_tokens_are_typeable(key: str):
    """Tokens compared with user input must be plain ASCII without spaces or tags."""
    value = _all_ja_values().get(key)
    assert value is not None, f"{key} is not defined in ja"
    assert re.fullmatch(r"[A-Za-z0-9]+", value), (
        f"{key}={value!r} must be a plain ASCII token"
    )
