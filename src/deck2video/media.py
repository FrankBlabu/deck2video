"""Thin, explicit wrappers around the ffmpeg / ffprobe executables."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

SAMPLE_RATE = 48000


class MediaError(RuntimeError):
    pass


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaError(f"{name} not found on PATH; install ffmpeg (e.g. apt-get install ffmpeg)")
    return path


def run(args: list[str], *, stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(args, input=stdin, capture_output=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-5:]
        raise MediaError(f"{Path(args[0]).name} failed: " + " | ".join(tail))
    return proc.stdout


def to_wav(src: Path, dst: Path) -> None:
    """Decode any audio to 48 kHz mono 16-bit PCM WAV (bit-exact, no metadata)."""
    run(
        [
            tool("ffmpeg"), "-v", "error", "-y", "-i", str(src),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            str(dst),
        ]
    )  # fmt: skip


def probe(path: Path) -> dict[str, Any]:
    out = run(
        [
            tool("ffprobe"), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
    )  # fmt: skip
    data: dict[str, Any] = json.loads(out)
    return data


def wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()
