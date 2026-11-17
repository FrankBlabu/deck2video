"""TTS adapter protocol, narration models and the on-disk narration cache."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from deck2video.media import to_wav, wav_duration
from deck2video.text import normalize_text, words


class TTSError(RuntimeError):
    pass


class Word(BaseModel):
    text: str
    start: float
    end: float


class Narration(BaseModel):
    """Audio (48 kHz mono WAV) plus one timestamp per word of the narration text."""

    audio: Path
    duration: float
    words: list[Word]


@dataclass(frozen=True)
class Synthesis:
    """Raw adapter output: encoded audio and per-word timestamps in seconds."""

    audio: bytes
    suffix: str
    words: list[Word]


class TTSAdapter(Protocol):
    name: str

    def identity(self) -> dict[str, Any]:
        """Everything that changes the audio (voice, model, settings, adapter version).

        Never includes secrets: it is hashed into cache keys.
        """
        ...

    def synthesize(self, text: str) -> Synthesis: ...


def alignment_to_words(
    text: str, chars: list[str], starts: list[float], ends: list[float]
) -> list[Word]:
    """Turn character-level timestamps into word-level ones.

    Words are maximal runs of non-whitespace characters, so the result lines up with
    :func:`deck2video.text.words` of the same text.
    """
    if not (len(chars) == len(starts) == len(ends)):
        raise TTSError("alignment arrays differ in length")
    if "".join(chars) != text:
        raise TTSError("alignment characters do not reproduce the narration text")
    out: list[Word] = []
    current: list[int] = []
    for i, ch in enumerate([*chars, " "]):
        if ch.isspace():
            if current:
                out.append(
                    Word(
                        text="".join(chars[j] for j in current),
                        start=float(starts[current[0]]),
                        end=float(ends[current[-1]]),
                    )
                )
                current = []
        else:
            current.append(i)
    return out


class NarrationCache:
    """Narrations stored by a hash of (text, adapter identity): reruns cost no API calls."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def key(self, adapter: TTSAdapter, text: str) -> str:
        payload = json.dumps(
            {"text": normalize_text(text), "adapter": adapter.name, "identity": adapter.identity()},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def narrate(self, adapter: TTSAdapter, text: str) -> tuple[Narration, bool]:
        """Return the narration and whether it came from the cache."""
        text = normalize_text(text)
        folder = self.root / self.key(adapter, text)
        meta = folder / "narration.json"
        if meta.is_file():
            return Narration.model_validate_json(meta.read_text(encoding="utf-8")), True
        result = adapter.synthesize(text)
        expected = words(text)
        got = [w.text for w in result.words]
        if got != expected:
            raise TTSError(
                f"{adapter.name}: timestamps cover {len(got)} words, the note has {len(expected)}"
            )
        folder.mkdir(parents=True, exist_ok=True)
        raw = folder / f"raw{result.suffix}"
        raw.write_bytes(result.audio)
        wav = folder / "audio.wav"
        to_wav(raw, wav)
        raw.unlink()
        narration = Narration(audio=wav, duration=wav_duration(wav), words=result.words)
        meta.write_text(narration.model_dump_json(indent=1), encoding="utf-8")
        return narration, False
