"""Word tokenisation shared by notes, cue anchors and TTS timestamps.

A *word* is a maximal run of non-whitespace characters of the normalised note text.
The same text is sent to the TTS engine, so word ``i`` of the note is word ``i`` of
the narration timestamps, and an anchor phrase maps to a word index range.
"""

from __future__ import annotations

import re
import unicodedata

_EDGE = re.compile(r"^[^\w]+|[^\w]+$")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize_text(text: str) -> str:
    """Collapse all whitespace to single spaces and strip the ends."""
    return " ".join(text.split())


def words(text: str) -> list[str]:
    """Split normalised text into words (whitespace-separated tokens)."""
    return normalize_text(text).split(" ") if text.strip() else []


def norm_word(word: str) -> str:
    """Comparison form of a word: NFKC, casefolded, unified quotes, edge punctuation off."""
    w = unicodedata.normalize("NFKC", word).translate(_QUOTES).casefold()
    return _EDGE.sub("", w)


def find_phrase(haystack: list[str], phrase: str) -> list[int]:
    """Return every word index where ``phrase`` starts in ``haystack``.

    Words that normalise to nothing (a lone dash) are skipped on both sides.
    """
    needle = [n for n in (norm_word(w) for w in words(phrase)) if n]
    if not needle:
        return []
    indexed = [(i, n) for i, n in ((i, norm_word(w)) for i, w in enumerate(haystack)) if n]
    norms = [n for _, n in indexed]
    hits: list[int] = []
    for k in range(len(norms) - len(needle) + 1):
        if norms[k : k + len(needle)] == needle:
            hits.append(indexed[k][0])
    return hits


def phrase_span(haystack: list[str], start: int, phrase: str) -> tuple[int, int]:
    """Word index range ``[start, end)`` covered by ``phrase`` starting at ``start``."""
    count = len([w for w in words(phrase) if norm_word(w)])
    end = start
    seen = 0
    while end < len(haystack) and seen < count:
        if norm_word(haystack[end]):
            seen += 1
        end += 1
    return start, end
