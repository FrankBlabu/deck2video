"""SRT and WebVTT captions built from the same word timestamps as the narration."""

from __future__ import annotations

import itertools
from pathlib import Path

from pydantic import BaseModel

from deck2video.tts import Word

MAX_LINE = 42
MAX_LINES = 2
MAX_DURATION = 6.0


class Caption(BaseModel):
    start: float
    end: float
    text: str


def captions(words: list[Word], offset: float = 0.0) -> list[Caption]:
    """Group words into captions of at most two 42-character lines and six seconds.

    A caption also ends after a word that ends a sentence.
    """
    out: list[Caption] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            out.append(
                Caption(
                    start=round(offset + current[0].start, 3),
                    end=round(offset + current[-1].end, 3),
                    text=_wrap([w.text for w in current]),
                )
            )
            current.clear()

    for w in words:
        if current:
            text = " ".join([*(c.text for c in current), w.text])
            too_long = len(text) > MAX_LINE * MAX_LINES or _lines(text) > MAX_LINES
            too_slow = w.end - current[0].start > MAX_DURATION
            if too_long or too_slow:
                flush()
        current.append(w)
        if w.text.endswith((".", "?", "!", ":", ";")):
            flush()
    flush()
    for a, b in itertools.pairwise(out):
        if a.end > b.start:
            a.end = b.start
    return out


def _lines(text: str) -> int:
    return len(_wrap(text.split(" ")).split("\n"))


def _wrap(tokens: list[str]) -> str:
    lines: list[str] = []
    line = ""
    for t in tokens:
        if line and len(line) + 1 + len(t) > MAX_LINE:
            lines.append(line)
            line = t
        else:
            line = f"{line} {t}" if line else t
    lines.append(line)
    return "\n".join(lines)


def _stamp(seconds: float, sep: str) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(caps: list[Caption], path: Path) -> None:
    blocks = [
        f"{i}\n{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}\n{c.text}\n"
        for i, c in enumerate(caps, 1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def write_vtt(caps: list[Caption], path: Path) -> None:
    blocks = [f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}\n{c.text}\n" for c in caps]
    path.write_text("WEBVTT\n\n" + "\n".join(blocks), encoding="utf-8")
