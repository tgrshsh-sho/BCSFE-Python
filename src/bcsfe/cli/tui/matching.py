"""Query normalisation and filtering for the list widgets.

Pure functions, no prompt_toolkit dependency.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

# Katakana (ァ U+30A1 .. ヶ U+30F6, ヽ ヾ) -> Hiragana (ぁ U+3041 .. ゖ U+3096, ゝ ゞ)
_KANA_FOLD: dict[int, int] = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}
_KANA_FOLD.update({0x30FD: 0x309D, 0x30FE: 0x309E})

# The editor's colour markup: ``<@t>text</>``; ``\<`` ``\>`` ``\/`` are literals.
_MARKUP = re.compile(r"\\([<>/])|<[^<>]*>")


def normalize(text: str) -> str:
    """NFKC + casefold + katakana->hiragana, so that ネコ, ねこ and ﾈｺ compare
    equal, as do full-width and half-width letters and digits."""
    return unicodedata.normalize("NFKC", text).casefold().translate(_KANA_FOLD)


def strip_markup(text: str) -> str:
    """Remove the editor's colour tags, keeping escaped special characters."""
    return _MARKUP.sub(lambda m: m.group(1) or "", text)


def split_query(query: str) -> list[str]:
    """Normalised search terms (split on any whitespace, incl. full-width)."""
    return normalize(query).split()


class Matcher:
    """Filters and orders candidates for a query.

    A candidate matches when every query term is a substring of its
    normalised label (markup stripped) or keywords.  Matches are ordered:
    labels starting with the whole query first, then labels starting with the
    first term (only differs for multi-term queries), then the other matches;
    each group keeps the original order.  An empty query matches everything in
    the original order.
    """

    def __init__(self, labels: Sequence[str], keywords: Sequence[str] = ()) -> None:
        self.labels = [normalize(strip_markup(label)).lstrip() for label in labels]
        self.haystacks: list[str] = []
        for i, label in enumerate(self.labels):
            kw = keywords[i] if i < len(keywords) else ""
            self.haystacks.append(label + "\x00" + normalize(kw) if kw else label)

    def __len__(self) -> int:
        return len(self.labels)

    def filter(self, query: str) -> list[int]:
        terms = split_query(query)
        if not terms:
            return list(range(len(self.labels)))
        if len(terms) == 1:
            term = terms[0]
            hits = [i for i, hay in enumerate(self.haystacks) if term in hay]
        else:
            hits = [
                i
                for i, hay in enumerate(self.haystacks)
                if all(term in hay for term in terms)
            ]
        whole = " ".join(terms)
        first = terms[0]
        tiers: tuple[list[int], list[int], list[int]] = ([], [], [])
        for i in hits:
            label = self.labels[i]
            if label.startswith(whole):
                tiers[0].append(i)
            elif label.startswith(first):
                tiers[1].append(i)
            else:
                tiers[2].append(i)
        return tiers[0] + tiers[1] + tiers[2]
