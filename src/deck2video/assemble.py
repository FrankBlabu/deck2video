"""ffmpeg assembly: per-slide video segments, fades, concatenation, the audio track.

Each slide becomes a video-only H.264 segment of exactly ``n_frames`` frames (narration
length + pad, on the 30 fps grid) and a WAV padded to exactly the same length. The final MP4
concatenates the video segments without re-encoding and encodes the joined audio to AAC
once, so audio and video stay sample-accurate across every join (no AAC priming drift).

The "fade between slides" is a dip through black: a slide whose ``data-transition`` is set
fades out over its tail, and the following slide fades in over its lead.
"""

from __future__ import annotations

import subprocess
import wave
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from deck2video.clock import FPS, frame_count
from deck2video.media import SAMPLE_RATE, MediaError, run, tool

WIDTH, HEIGHT = 1920, 1080
BITEXACT = ["-fflags", "+bitexact", "-map_metadata", "-1"]


class Quality(BaseModel):
    preset: str = "veryfast"
    crf: int = 20

    @classmethod
    def best(cls) -> Quality:
        return cls(preset="slow", crf=16)


class Pad(BaseModel):
    """Silence around the narration: ``lead`` before it, ``tail`` after (seconds)."""

    lead: float = 0.3
    tail: float = 0.9
    fade_in: float = 0.3
    fade_out: float = 0.6

    @property
    def total(self) -> float:
        return self.lead + self.tail


class SegmentTiming(BaseModel):
    frames: int
    duration: float
    lead: float


def segment_timing(audio_duration: float, pad: Pad, fps: int = FPS) -> SegmentTiming:
    """Video length = audio length + pad, rounded to the frame grid."""
    frames = frame_count(audio_duration + pad.total, fps)
    return SegmentTiming(frames=frames, duration=frames / fps, lead=pad.lead)


def encode_segment(
    frames: Iterable[bytes],
    out: Path,
    n_frames: int,
    *,
    fps: int = FPS,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    quality: Quality | None = None,
) -> None:
    """Pipe PNG frames into a video-only H.264 segment (yuv420p, 1920x1080, ``fps``)."""
    quality = quality or Quality()
    duration = n_frames / fps
    filters = [f"scale={WIDTH}:{HEIGHT}:flags=bicubic"]
    if fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        filters.append(f"fade=t=out:st={max(0.0, duration - fade_out):.4f}:d={fade_out}")
    filters.append("format=yuv420p")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    args = [
        tool("ffmpeg"), "-v", "error", "-y",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
        "-vf", ",".join(filters), "-frames:v", str(n_frames), "-r", str(fps),
        "-c:v", "libx264", "-preset", quality.preset, "-crf", str(quality.crf),
        "-g", str(fps * 2), "-threads", "1", "-flags:v", "+bitexact",
        "-an", *BITEXACT, "-movflags", "+faststart", str(tmp),
    ]  # fmt: skip
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    written = 0
    try:
        for frame in frames:
            proc.stdin.write(frame)
            written += 1
        proc.stdin.close()
    except BrokenPipeError:
        pass
    stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise MediaError(f"ffmpeg failed encoding {out.name}: {stderr.strip()[-500:]}")
    if written != n_frames:
        raise MediaError(f"{out.name}: expected {n_frames} frames, got {written}")
    tmp.replace(out)


def pad_audio(narration: Path, out: Path, lead: float, duration: float) -> None:
    """Write ``lead`` s of silence, the narration, then silence up to exactly ``duration`` s."""
    with wave.open(str(narration), "rb") as src:
        if src.getframerate() != SAMPLE_RATE or src.getnchannels() != 1 or src.getsampwidth() != 2:
            raise MediaError(f"{narration}: expected 48 kHz mono 16-bit PCM")
        pcm = src.readframes(src.getnframes())
    total = round(duration * SAMPLE_RATE)
    lead_n = round(lead * SAMPLE_RATE)
    body = b"\x00\x00" * lead_n + pcm
    body = body[: total * 2] + b"\x00\x00" * max(0, total - len(body) // 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(SAMPLE_RATE)
        dst.writeframes(body)


def concat_audio(parts: list[Path], out: Path) -> None:
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(SAMPLE_RATE)
        for part in parts:
            with wave.open(str(part), "rb") as src:
                dst.writeframes(src.readframes(src.getnframes()))


def concat(segments: list[Path], audio: list[Path], out: Path, workdir: Path) -> None:
    """Join video segments (stream copy) and mux the joined audio as AAC."""
    if not segments or len(segments) != len(audio):
        raise MediaError("need one audio part per video segment")
    workdir.mkdir(parents=True, exist_ok=True)
    listing = workdir / "segments.txt"
    listing.write_text(
        "".join(f"file '{s.resolve().as_posix()}'\n" for s in segments), encoding="utf-8"
    )
    joined = workdir / "audio.wav"
    concat_audio(audio, joined)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    run(
        [
            tool("ffmpeg"), "-v", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(joined),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
            *BITEXACT, "-movflags", "+faststart", str(tmp),
        ]
    )  # fmt: skip
    tmp.replace(out)
