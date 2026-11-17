"""ElevenLabs text-to-speech with character timestamps (the default voice service).

The API key is read from ``ELEVENLABS_API_KEY``; it is sent only in the request header and
never logged, cached or written to disk.
"""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

from deck2video.tts.base import Synthesis, TTSError, alignment_to_words

API = "https://api.elevenlabs.io"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"
"""ElevenLabs premade voice "George" (warm, British, narration)."""
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
ADAPTER_VERSION = 1


class ElevenLabsAdapter:
    name = "elevenlabs"

    def __init__(
        self,
        voice: str | None = None,
        model: str | None = None,
        *,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        seed: int = 1,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 4,
    ) -> None:
        self.voice = voice or os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
        self.model = model or os.environ.get("ELEVENLABS_MODEL_ID") or DEFAULT_MODEL
        self.settings = {"stability": stability, "similarity_boost": similarity_boost}
        self.seed = seed
        self._api_key = api_key
        self._client = client
        self._sleep = sleep
        self._retries = retries

    def identity(self) -> dict[str, Any]:
        return {
            "version": ADAPTER_VERSION,
            "voice": self.voice,
            "model": self.model,
            "settings": self.settings,
            "seed": self.seed,
            "format": OUTPUT_FORMAT,
        }

    def _key(self) -> str:
        key = self._api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise TTSError(
                "ELEVENLABS_API_KEY is not set; export your ElevenLabs API key "
                "(or use --voice-adapter fake for an offline test build)"
            )
        return key

    def synthesize(self, text: str) -> Synthesis:
        key = self._key()
        url = f"{API}/v1/text-to-speech/{self.voice}/with-timestamps"
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": self.settings,
            "seed": self.seed,
        }
        client = self._client or httpx.Client(timeout=120)
        try:
            data = self._post(client, url, body, key)
        finally:
            if self._client is None:
                client.close()
        try:
            audio = base64.b64decode(data["audio_base64"])
            al = data["alignment"]
            words = alignment_to_words(
                text,
                al["characters"],
                al["character_start_times_seconds"],
                al["character_end_times_seconds"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TTSError(f"unexpected ElevenLabs response: {exc!r}") from exc
        return Synthesis(audio=audio, suffix=".mp3", words=words)

    def _post(self, client: httpx.Client, url: str, body: dict[str, Any], key: str) -> Any:
        delay = 1.0
        for attempt in range(self._retries + 1):
            try:
                resp = client.post(
                    url,
                    params={"output_format": OUTPUT_FORMAT},
                    json=body,
                    headers={"xi-api-key": key, "accept": "application/json"},
                )
            except httpx.TransportError as exc:
                if attempt == self._retries:
                    raise TTSError(f"ElevenLabs unreachable: {exc}") from exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 403):
                    raise TTSError(
                        f"ElevenLabs rejected the API key ({resp.status_code}); check ELEVENLABS_API_KEY"
                    )
                if resp.status_code == 404:
                    raise TTSError(f"ElevenLabs voice {self.voice!r} not found (404)")
                if resp.status_code != 429 and resp.status_code < 500:
                    raise TTSError(f"ElevenLabs error {resp.status_code}: {resp.text[:300]}")
                if attempt == self._retries:
                    raise TTSError(
                        f"ElevenLabs error {resp.status_code} after {attempt + 1} attempts"
                    )
            self._sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")
