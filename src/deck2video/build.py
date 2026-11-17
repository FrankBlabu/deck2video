"""``deck2video build``: check, narrate, render, assemble – with a per-slide segment cache.

Each slide's segment (video, padded audio, timeline, frame hashes) is cached under a hash of
everything that can change it: the slide HTML, ``deck.css``, the assets and fonts it uses,
its note and cues, the voice, the timing and quality settings, the neighbouring fades and
the tool version. Editing one note rebuilds one slide.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from deck2video import __version__
from deck2video.assemble import (
    Pad,
    Quality,
    concat,
    encode_segment,
    pad_audio,
    segment_timing,
)
from deck2video.clock import FPS, FrameStats, render_frames
from deck2video.cues import ResolvedCue, check_deck, select_slides
from deck2video.deck import BLOB_RE, Deck, Slide, parse_deck
from deck2video.effects import EffectsRunner, schedule
from deck2video.fonts import FontCache, default_cache_root
from deck2video.problems import DeckError
from deck2video.render import Renderer
from deck2video.subtitles import Caption, captions, write_srt, write_vtt
from deck2video.tts import Narration, NarrationCache, TTSAdapter, get_adapter

Log = Callable[[str], None]


class BuildOptions(BaseModel):
    deck: Path
    output: Path = Path("talk.mp4")
    build_dir: Path = Path("build")
    slides: list[str] | None = None
    adapter: str = "elevenlabs"
    voice: str | None = None
    model: str | None = None
    best: bool = False
    pad: Pad = Pad()
    cache_root: Path | None = None


class SlideReport(BaseModel):
    id: str
    status: Literal["cached", "rendered"]
    key: str
    frames: int
    captured: int
    duration: float
    narration_cached: bool
    seconds: float


class BuildReport(BaseModel):
    output: Path
    srt: Path
    vtt: Path
    duration: float
    slides: list[SlideReport]

    @property
    def rendered(self) -> list[str]:
        return [s.id for s in self.slides if s.status == "rendered"]


class SegmentMeta(BaseModel):
    key: str
    frames: int
    duration: float
    lead: float
    captured: int
    words: list[dict[str, object]]


class BlockedRequests(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def segment_key(
    deck: Deck,
    slide: Slide,
    cues: list[ResolvedCue],
    narration_key: str,
    font_css: str,
    fades: tuple[bool, bool],
    opts: BuildOptions,
) -> str:
    blobs = sorted(set(slide.blobs) | set(BLOB_RE.findall(deck.css)))
    payload = {
        "tool": __version__,
        "slide": _sha(slide.html.encode()),
        "css": _sha(deck.css.encode()),
        "assets": {b: _sha(deck.assets[b].read_bytes()) for b in blobs if b in deck.assets},
        "fonts": _sha(font_css.encode()),
        "note": slide.note,
        "cues": [c.model_dump(mode="json") for c in cues],
        "narration": narration_key,
        "pad": opts.pad.model_dump(),
        "quality": (Quality.best() if opts.best else Quality()).model_dump(),
        "fades": fades,
        "fps": FPS,
        "page": [s.id for s in deck.visible_slides].index(slide.id) + 1,
    }
    return _sha(json.dumps(payload, sort_keys=True).encode())[:24]


def build(opts: BuildOptions, log: Log = print) -> BuildReport:
    deck, problems = parse_deck(opts.deck)
    if deck is None:
        raise DeckError(problems)
    cues, cue_problems = check_deck(deck, opts.slides)
    problems += cue_problems
    if problems:
        raise DeckError(problems)
    slides = select_slides(deck, opts.slides, [])

    cache_root = opts.cache_root or default_cache_root()
    fonts = FontCache(cache_root)
    font_css = fonts.deck_css(deck)
    adapter: TTSAdapter = get_adapter(opts.adapter, opts.voice, opts.model)
    narrations = NarrationCache(cache_root / "tts")
    quality = Quality.best() if opts.best else Quality()
    pad = opts.pad
    seg_root = opts.build_dir / "cache" / "segments"

    renderer: Renderer | None = None
    reports: list[SlideReport] = []
    segments: list[Path] = []
    audio: list[Path] = []
    all_captions: list[Caption] = []
    offset = 0.0
    try:
        for n, slide in enumerate(slides):
            started = time.monotonic()
            prefix = f"[{n + 1}/{len(slides)}] {slide.id}"
            narration, narration_cached = narrations.narrate(adapter, slide.note)
            fade_in = n > 0 and _fades(slides[n - 1])
            fade_out = _fades(slide)
            key = segment_key(
                deck,
                slide,
                cues[slide.id],
                narrations.key(adapter, slide.note),
                font_css,
                (fade_in, fade_out),
                opts,
            )
            folder = seg_root / key
            meta_file = folder / "meta.json"
            if meta_file.is_file():
                meta = SegmentMeta.model_validate_json(meta_file.read_text(encoding="utf-8"))
                status: Literal["cached", "rendered"] = "cached"
            else:
                if renderer is None:
                    renderer = Renderer(deck, fonts).__enter__()
                meta = _render_slide(
                    renderer, slide, cues[slide.id], narration, folder, key, pad, quality,
                    (fade_in, fade_out),
                )  # fmt: skip
                status = "rendered"
            _publish(folder, opts.build_dir / slide.id)
            segments.append(folder / "segment.mp4")
            audio.append(folder / "audio.wav")
            all_captions += captions(narration.words, offset + meta.lead)
            offset += meta.duration
            slide_report = SlideReport(
                id=slide.id,
                status=status,
                key=key,
                frames=meta.frames,
                captured=meta.captured,
                duration=meta.duration,
                narration_cached=narration_cached,
                seconds=round(time.monotonic() - started, 2),
            )
            reports.append(slide_report)
            detail = (
                "cached"
                if status == "cached"
                else f"rendered {meta.frames} frames ({meta.captured} captured)"
            )
            log(f"{prefix}: {detail}, {meta.duration:.1f}s, {slide_report.seconds:.1f}s elapsed")
    finally:
        if renderer is not None:
            renderer.__exit__(None, None, None)

    concat(segments, audio, opts.output, opts.build_dir / "cache" / "concat")
    srt = opts.output.with_suffix(".srt")
    vtt = opts.output.with_suffix(".vtt")
    write_srt(all_captions, srt)
    write_vtt(all_captions, vtt)
    log(f"wrote {opts.output} ({offset:.1f}s), {srt.name}, {vtt.name}")
    report = BuildReport(output=opts.output, srt=srt, vtt=vtt, duration=offset, slides=reports)
    (opts.build_dir / "report.json").write_text(report.model_dump_json(indent=1), encoding="utf-8")
    return report


def _fades(slide: Slide) -> bool:
    return bool(slide.transition) and slide.transition != "none"


def _render_slide(
    renderer: Renderer,
    slide: Slide,
    cues: list[ResolvedCue],
    narration: Narration,
    folder: Path,
    key: str,
    pad: Pad,
    quality: Quality,
    fades: tuple[bool, bool],
) -> SegmentMeta:
    timing = segment_timing(narration.duration, pad)
    tmp = folder.with_name(folder.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    page = renderer.open(slide)
    try:
        if page.blocked:
            raise BlockedRequests(
                f"slides/{slide.id}.html: blocked network request(s): {', '.join(page.blocked)}"
            )
        runner = EffectsRunner(page, schedule(cues, narration.words, offset=timing.lead))
        stats = FrameStats()
        hashes: list[str] = []

        def frames() -> Iterator[bytes]:
            for frame in render_frames(page, timing.frames, overlay=runner, stats=stats):
                hashes.append(_sha(frame))
                yield frame

        encode_segment(
            frames(),
            tmp / "segment.mp4",
            timing.frames,
            fade_in=pad.fade_in if fades[0] else 0.0,
            fade_out=pad.fade_out if fades[1] else 0.0,
            quality=quality,
        )
        runner.timeline(slide.id, timing.duration).write(tmp / "timeline.json")
    finally:
        page.close()
    pad_audio(narration.audio, tmp / "audio.wav", timing.lead, timing.duration)
    (tmp / "frames.sha256").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    meta = SegmentMeta(
        key=key,
        frames=timing.frames,
        duration=timing.duration,
        lead=timing.lead,
        captured=stats.captured,
        words=[w.model_dump() for w in narration.words],
    )
    (tmp / "meta.json").write_text(meta.model_dump_json(indent=1), encoding="utf-8")
    if folder.exists():
        shutil.rmtree(folder)
    tmp.replace(folder)
    return meta


def _publish(folder: Path, dest: Path) -> None:
    """Expose the slide's text twin at ``build/<id>/``."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("timeline.json", "frames.sha256", "meta.json"):
        shutil.copyfile(folder / name, dest / name)
