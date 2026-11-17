"""Pluggable narration: adapters return audio plus word-level timestamps."""

from __future__ import annotations

from deck2video.tts.base import (
    Narration,
    NarrationCache,
    Synthesis,
    TTSAdapter,
    TTSError,
    Word,
    alignment_to_words,
)
from deck2video.tts.elevenlabs import ElevenLabsAdapter
from deck2video.tts.fake import FakeAdapter

ADAPTERS = ("elevenlabs", "fake")


def get_adapter(name: str, voice: str | None = None, model: str | None = None) -> TTSAdapter:
    if name == "elevenlabs":
        return ElevenLabsAdapter(voice=voice, model=model)
    if name == "fake":
        return FakeAdapter()
    raise TTSError(f"unknown voice adapter {name!r}; choose one of {', '.join(ADAPTERS)}")


__all__ = [
    "ADAPTERS",
    "ElevenLabsAdapter",
    "FakeAdapter",
    "Narration",
    "NarrationCache",
    "Synthesis",
    "TTSAdapter",
    "TTSError",
    "Word",
    "alignment_to_words",
    "get_adapter",
]
