"""``local`` voice: Piper neural TTS, fully offline after a one-time model download.

Piper does not report word timings for its standard voices, so the synthesised audio goes
through the same forced alignment as recordings (:mod:`deck2video.tts.align`).
Install with ``pip install 'deck2video[local]'``.
"""

from __future__ import annotations

import io
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deck2video.media import wav_duration
from deck2video.tts.align import Recognizer, WhisperRecognizer, align
from deck2video.tts.base import Synthesis, TTSError

DEFAULT_PIPER_VOICE = "en_US-lessac-medium"
ADAPTER_VERSION = 1

Synth = Callable[[str], bytes]
"""Text -> WAV bytes."""


class LocalAdapter:
    name = "local"

    def __init__(
        self,
        voice: str | None = None,
        *,
        cache_root: Path | None = None,
        recognizer: Recognizer | None = None,
        synth: Synth | None = None,
        align_model: str | None = None,
    ) -> None:
        self.voice = voice or DEFAULT_PIPER_VOICE
        self.cache_root = cache_root
        self.recognizer = recognizer or WhisperRecognizer(align_model, cache_root)
        self._synth = synth
        self._piper: Any = None

    def identity(self) -> dict[str, Any]:
        return {
            "version": ADAPTER_VERSION,
            "engine": "piper",
            "voice": self.voice,
            "align": self.recognizer.identity(),
        }

    def _piper_synth(self, text: str) -> bytes:
        if self._piper is None:
            try:
                from piper import PiperVoice
                from piper.download_voices import download_voice
            except ImportError as exc:
                raise TTSError(
                    "the local voice needs Piper: pip install 'deck2video[local]'"
                ) from exc
            folder = (self.cache_root or Path.home() / ".cache" / "deck2video") / "piper"
            folder.mkdir(parents=True, exist_ok=True)
            model = folder / f"{self.voice}.onnx"
            if not model.is_file():
                try:
                    download_voice(self.voice, folder)
                except Exception as exc:
                    raise TTSError(
                        f"could not download Piper voice {self.voice!r} from Hugging Face: {exc}"
                    ) from exc
            self._piper = PiperVoice.load(model)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self._piper.synthesize_wav(text, wf)
        return buf.getvalue()

    def synthesize(self, text: str, slide_id: str | None = None) -> Synthesis:
        audio = (self._synth or self._piper_synth)(text)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "voice.wav"
            wav.write_bytes(audio)
            heard = self.recognizer.hear(wav)
            duration = wav_duration(wav)
        return Synthesis(audio=audio, suffix=".wav", words=align(text, heard, duration))
