"""Forced alignment: word timestamps for known text in given audio.

A speech recogniser (Whisper, via faster-whisper) transcribes the audio with word timings;
its words are then matched to the note's words with a sequence alignment. Matched words take
the recogniser's times, a run of differently written words ("1,000" for "a thousand") shares
the time span of what was heard, and words the recogniser missed are spread over the gap
between their neighbours. Extra recognised words (noise, hallucinations) are ignored. The
result always has exactly one timestamp per note word, in order.
"""

from __future__ import annotations

import difflib
import os
import subprocess
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from deck2video.media import tool
from deck2video.text import norm_word, words
from deck2video.tts.base import TTSError, Word

DEFAULT_ALIGN_MODEL = "base.en"


class Heard(BaseModel):
    """A word as the recogniser heard it."""

    text: str
    start: float
    end: float


class Recognizer(Protocol):
    def identity(self) -> dict[str, Any]: ...

    def hear(self, wav: Path) -> list[Heard]: ...


class WhisperRecognizer:
    """faster-whisper on CPU; the model is downloaded once into the cache."""

    def __init__(self, model: str | None = None, cache_root: Path | None = None) -> None:
        self.model = model or os.environ.get("DECK2VIDEO_ALIGN_MODEL") or DEFAULT_ALIGN_MODEL
        self.cache_root = cache_root
        self._model: Any = None

    def identity(self) -> dict[str, Any]:
        return {"recognizer": "faster-whisper", "model": self.model}

    def _load(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TTSError(
                    "forced alignment needs faster-whisper: pip install 'deck2video[recorded]'"
                ) from exc
            # plain HTTPS downloads work behind proxies where the Xet transfer protocol does not
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            root = str(self.cache_root / "whisper") if self.cache_root else None
            self._model = WhisperModel(
                self.model, device="cpu", compute_type="int8", download_root=root
            )
        return self._model

    def hear(self, wav: Path) -> list[Heard]:
        import numpy as np

        pcm = subprocess.run(
            [tool("ffmpeg"), "-v", "error", "-i", str(wav), "-ac", "1", "-ar", "16000",
             "-f", "f32le", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        audio = np.frombuffer(pcm, dtype=np.float32)
        language = "en" if self.model.endswith(".en") else None
        segments, _ = self._load().transcribe(
            audio,
            word_timestamps=True,
            language=language,
            beam_size=5,
            condition_on_previous_text=False,
        )
        out: list[Heard] = []
        for seg in segments:
            for w in seg.words or []:
                out.append(Heard(text=w.word, start=float(w.start), end=float(w.end)))
        return out


def merge_pieces(heard: list[Heard]) -> list[Heard]:
    """Join recogniser tokens that continue a word (no leading space), e.g. ``0`` + ``.1``."""
    out: list[Heard] = []
    for h in heard:
        if out and h.text and not h.text[0].isspace():
            prev = out[-1]
            out[-1] = Heard(text=prev.text + h.text, start=prev.start, end=h.end)
        else:
            out.append(Heard(text=h.text.strip(), start=h.start, end=h.end))
    return [h for h in out if h.text]


def _spread(tokens: list[str], start: float, end: float) -> list[tuple[float, float]]:
    """Split ``[start, end]`` over tokens in proportion to their length."""
    weights = [max(1, len(t)) for t in tokens]
    total = sum(weights)
    out = []
    t = start
    for w in weights:
        d = (end - start) * w / total
        out.append((t, t + d))
        t += d
    return out


def align(text: str, heard: list[Heard], duration: float) -> list[Word]:
    """One timestamp per word of ``text`` (see module docstring)."""
    note = words(text)
    rec = merge_pieces(heard)
    times: list[tuple[float, float] | None] = [None] * len(note)
    matcher = difflib.SequenceMatcher(
        None, [norm_word(w) for w in note], [norm_word(h.text) for h in rec], autojunk=False
    )
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (rec[j1 + k].start, rec[j1 + k].end)
        elif op == "replace":
            shared = _spread(note[i1:i2], rec[j1].start, rec[j2 - 1].end)
            for k, t in enumerate(shared):
                times[i1 + k] = t
    # fill words the recogniser missed from the gap between their neighbours
    i = 0
    while i < len(note):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(note) and times[j] is None:
            j += 1
        before = times[i - 1] if i > 0 else None
        after = times[j] if j < len(note) else None
        left = before[1] if before is not None else 0.0
        right = after[0] if after is not None else duration
        for k, gap in enumerate(_spread(note[i:j], left, max(left, right))):
            times[i + k] = gap
        i = j
    out: list[Word] = []
    last = 0.0
    for w, span in zip(note, times, strict=True):
        assert span is not None
        start = max(last, round(span[0], 3))
        end = max(start, round(span[1], 3))
        out.append(Word(text=w, start=start, end=end))
        last = start
    return out
