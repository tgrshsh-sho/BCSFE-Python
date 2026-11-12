"""How often and how recently each feature was used, for the home's
"frequently used" section.

Stored in ``<data dir>/fork/usage.json``::

    {"version": 1, "features": {"xp": {"count": 3, "last": 1762851234.5}}}

Features are identified by the locale key of their name (e.g. ``xp``).  A
missing or unreadable file means no usage yet; a write that fails is ignored.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import cast

from bcsfe import core

VERSION = 1
LIMIT = 6
# Shown until there is enough usage: the features of the usual session
# (upload for new codes, unlock / upgrade cats, XP, NP, catfruit).
SEEDS = ("save_upload", "unlock_remove_cats", "upgrade_cats", "xp", "np", "catfruit")


@dataclass
class Entry:
    count: int
    last: float


def default_path() -> core.Path:
    return core.Path.get_data_folder().add("fork", "usage.json")


def _mapping(value: object) -> dict[str, object] | None:
    """``value`` if it is a JSON object (its keys are strings)."""
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value)


class Usage:
    def __init__(
        self, path: core.Path | None = None, entries: dict[str, Entry] | None = None
    ) -> None:
        self.path = path if path is not None else default_path()
        self.entries: dict[str, Entry] = entries if entries is not None else {}

    @staticmethod
    def load(path: core.Path | None = None) -> Usage:
        usage = Usage(path)
        try:
            with open(usage.path.path, encoding="utf-8") as f:
                data = _mapping(json.load(f))
        except (OSError, ValueError):
            return usage
        features = _mapping(data.get("features")) if data is not None else None
        for name, value in (features or {}).items():
            entry = Usage._entry(value)
            if entry is not None:
                usage.entries[name] = entry
        return usage

    @staticmethod
    def _entry(value: object) -> Entry | None:
        fields = _mapping(value)
        if fields is None:
            return None
        count = fields.get("count")
        last = fields.get("last", 0.0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return None
        if isinstance(last, bool) or not isinstance(last, (int, float)):
            return None
        return Entry(count, float(last))

    def snapshot(self) -> Usage:
        """A copy that later :meth:`record` calls on this object do not change."""
        entries = {name: Entry(e.count, e.last) for name, e in self.entries.items()}
        return Usage(self.path, entries)

    def record(self, name: str) -> None:
        """Count one use of ``name`` now and save the file."""
        newest = max((e.last for e in self.entries.values()), default=0.0)
        now = max(time.time(), newest + 1e-6)  # strictly newer than any other
        entry = self.entries.get(name)
        if entry is None:
            self.entries[name] = Entry(1, now)
        else:
            entry.count += 1
            entry.last = now
        self.save()

    def save(self) -> None:
        data = {
            "version": VERSION,
            "features": {
                name: {"count": e.count, "last": e.last}
                for name, e in self.entries.items()
            },
        }
        partial = self.path.path + ".partial"
        try:
            self.path.parent().generate_dirs()
            with open(partial, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(partial, self.path.path)
        except OSError:
            pass

    def ranked(self, available: Collection[str]) -> list[str]:
        """Used features among ``available``: most used first, then the most
        recently used."""
        names = [name for name in self.entries if name in available]
        names.sort(key=lambda n: (-self.entries[n].count, -self.entries[n].last))
        return names

    def favourites(
        self,
        available: Collection[str],
        limit: int = LIMIT,
        seeds: Sequence[str] = SEEDS,
    ) -> list[str]:
        """Up to ``limit`` features for the "frequently used" section: the
        ranked used ones, filled up with the ``seeds`` that exist."""
        names = self.ranked(available)[:limit]
        for seed in seeds:
            if len(names) >= limit:
                break
            if seed in available and seed not in names:
                names.append(seed)
        return names
