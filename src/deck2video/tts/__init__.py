"""Pluggable narration: adapters return audio plus word-level timestamps."""

from __future__ import annotations

from pathlib import Path

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
from deck2video.tts.local import LocalAdapter
from deck2video.tts.recorded import RecordedAdapter

ADAPTERS = ("elevenlabs", "local", "recorded", "fake")


def get_adapter(
    name: str,
    voice: str | None = None,
    model: str | None = None,
    *,
    recordings: Path | None = None,
    cache_root: Path | None = None,
    align_model: str | None = None,
) -> TTSAdapter:
    """``voice`` is an ElevenLabs voice id or a Piper voice name; ``model`` an ElevenLabs model."""
    if name == "elevenlabs":
        return ElevenLabsAdapter(voice=voice, model=model)
    if name == "local":
        return LocalAdapter(voice, cache_root=cache_root, align_model=align_model)
    if name == "recorded":
        if recordings is None:
            raise TTSError("the recorded voice needs a recordings folder")
        return RecordedAdapter(recordings, cache_root=cache_root, align_model=align_model)
    if name == "fake":
        return FakeAdapter()
    raise TTSError(f"unknown voice adapter {name!r}; choose one of {', '.join(ADAPTERS)}")


__all__ = [
    "ADAPTERS",
    "ElevenLabsAdapter",
    "FakeAdapter",
    "LocalAdapter",
    "Narration",
    "NarrationCache",
    "RecordedAdapter",
    "Synthesis",
    "TTSAdapter",
    "TTSError",
    "Word",
    "alignment_to_words",
    "get_adapter",
]
