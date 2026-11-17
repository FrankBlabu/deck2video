"""Deterministic offline adapter for tests and CI: soft tones, computed timestamps."""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import Any

from deck2video.text import words
from deck2video.tts.base import Synthesis, Word

RATE = 48000


class FakeAdapter:
    name = "fake"

    def __init__(self, lead: float = 0.2, gap: float = 0.08, per_char: float = 0.055) -> None:
        self.lead = lead
        self.gap = gap
        self.per_char = per_char

    def identity(self) -> dict[str, Any]:
        return {"version": 1, "lead": self.lead, "gap": self.gap, "per_char": self.per_char}

    def synthesize(self, text: str) -> Synthesis:
        t = self.lead
        out: list[Word] = []
        for token in words(text):
            dur = round(0.12 + self.per_char * len(token), 3)
            out.append(Word(text=token, start=round(t, 3), end=round(t + dur, 3)))
            t += dur + self.gap
        total = t + self.lead
        n = int(total * RATE)
        samples = bytearray(n * 2)
        for w in out:
            a, b = int(w.start * RATE), int(w.end * RATE)
            for i in range(a, min(b, n)):
                v = int(3000 * math.sin(2 * math.pi * 220 * (i - a) / RATE))
                struct.pack_into("<h", samples, i * 2, v)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(bytes(samples))
        return Synthesis(audio=buf.getvalue(), suffix=".wav", words=out)
