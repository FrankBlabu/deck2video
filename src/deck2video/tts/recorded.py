"""``recorded`` voice: your own recording per slide, with forced alignment for timestamps.

Put one audio file per slide in the recordings folder (default ``<deck>/recordings``), named
after the slide id: ``scale.wav`` (``.mp3``, ``.m4a``, ``.flac`` and ``.ogg`` work too). Read the
slide's note as written; the words are located in the audio by forced alignment.
Install with ``pip install 'deck2video[recorded]'``.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from deck2video.media import to_wav, wav_duration
from deck2video.problems import Problem
from deck2video.tts.align import Recognizer, WhisperRecognizer, align
from deck2video.tts.base import Synthesis, TTSError

EXTENSIONS = (".wav", ".mp3", ".m4a", ".flac", ".ogg")
ADAPTER_VERSION = 1


class RecordedAdapter:
    name = "recorded"

    def __init__(
        self,
        folder: Path,
        *,
        cache_root: Path | None = None,
        recognizer: Recognizer | None = None,
        align_model: str | None = None,
    ) -> None:
        self.folder = folder
        self.recognizer = recognizer or WhisperRecognizer(align_model, cache_root)

    def identity(self) -> dict[str, Any]:
        return {"version": ADAPTER_VERSION, "align": self.recognizer.identity()}

    def recording(self, slide_id: str) -> Path:
        found = [self.folder / f"{slide_id}{ext}" for ext in EXTENSIONS]
        found = [p for p in found if p.is_file()]
        if not found:
            raise TTSError(
                f"no recording for slide {slide_id!r}: expected {self.folder / slide_id}.wav"
            )
        if len(found) > 1:
            names = ", ".join(p.name for p in found)
            raise TTSError(f"more than one recording for slide {slide_id!r}: {names}")
        return found[0]

    def slide_identity(self, slide_id: str | None) -> dict[str, Any]:
        """The recording's content is part of the cache key: re-record, re-align."""
        if slide_id is None:
            raise TTSError("the recorded voice needs to know which slide it narrates")
        data = self.recording(slide_id).read_bytes()
        return {"recording": hashlib.sha256(data).hexdigest()}

    def problems(self, slide_ids: list[str]) -> list[Problem]:
        out = []
        for sid in slide_ids:
            try:
                self.recording(sid)
            except TTSError as exc:
                out.append(Problem(where=f"recordings/{sid}", message=str(exc)))
        return out

    def synthesize(self, text: str, slide_id: str | None = None) -> Synthesis:
        if slide_id is None:
            raise TTSError("the recorded voice needs to know which slide it narrates")
        source = self.recording(slide_id)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "voice.wav"
            to_wav(source, wav)
            heard = self.recognizer.hear(wav)
            duration = wav_duration(wav)
        if not heard:
            raise TTSError(f"{source.name}: no speech recognised")
        return Synthesis(
            audio=source.read_bytes(), suffix=source.suffix, words=align(text, heard, duration)
        )
