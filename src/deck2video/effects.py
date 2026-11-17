"""Effects engine: cue timing, per-frame effect state, overlay application, timeline.json.

Timing: a cue starts on the first word of its anchor phrase, at that word's timestamp from
the narration, quantised to the nearest frame. Each effect then eases in (``ease``), holds
(``hold``) and eases out (``ease``); ``reveal`` keeps its children visible to the end.

Camera effects (``zoom``, ``pan``) share one camera: if two overlap, the one that started
later takes over. Highlight effects (``spotlight``, ``outline``, ``pulse``) stack.

Everything here is a pure function of the frame time; the browser only draws it and reports
the target's bounding box, which goes into ``timeline.json`` (the text twin of the video).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from deck2video.clock import FPS
from deck2video.cues import Outline, Pan, Pulse, ResolvedCue, Reveal, Spotlight, Zoom
from deck2video.render import SlidePage
from deck2video.tts import Word

Phase = Literal["pending", "in", "hold", "out", "done"]


class ScheduledCue(BaseModel):
    resolved: ResolvedCue
    anchor_time: float
    """Timestamp (s) of the anchor's first word."""
    start_frame: int
    start: float
    """Start time (s) on the frame grid."""

    @property
    def key(self) -> str:
        return f"{self.resolved.index}"

    @property
    def end(self) -> float:
        cue = self.resolved.cue
        if isinstance(cue, Reveal):
            return self.start + max(0, self.children - 1) * cue.step + cue.ease
        return self.start + 2 * cue.ease + cue.hold

    children: int = 0


def schedule(
    cues: list[ResolvedCue], words: list[Word], fps: int = FPS, offset: float = 0.0
) -> list[ScheduledCue]:
    """Start each cue on its anchor word; ``offset`` is where the narration starts (s)."""
    out = []
    for r in cues:
        anchor = offset + words[r.word_start].start
        frame = round(anchor * fps)
        out.append(
            ScheduledCue(resolved=r, anchor_time=anchor, start_frame=frame, start=frame / fps)
        )
    return out


def smooth(x: float) -> float:
    """Ease in-out (smoothstep), clamped to [0, 1]."""
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def envelope(t: float, start: float, ease: float, hold: float) -> tuple[Phase, float]:
    """Phase and progress (0..1) of an ease-in / hold / ease-out effect at time ``t``."""
    if t < start:
        return "pending", 0.0
    if t < start + ease:
        return "in", smooth((t - start) / ease) if ease > 0 else 1.0
    if t < start + ease + hold:
        return "hold", 1.0
    if t < start + 2 * ease + hold:
        return "out", smooth(1 - (t - start - ease - hold) / ease) if ease > 0 else 0.0
    return "done", 0.0


class EffectFrame(BaseModel):
    cue: int
    effect: str
    target: str
    phase: Phase
    progress: float
    box: tuple[float, float, float, float] | None = None
    """Target's bounding box on the 1920x1080 frame, after the camera: x, y, w, h."""


class CameraFrame(BaseModel):
    scale: float = 1.0
    tx: float = 0.0
    ty: float = 0.0


class TimelineFrame(BaseModel):
    i: int
    t: float
    camera: CameraFrame
    effects: list[EffectFrame]


class TimelineCue(BaseModel):
    cue: int
    effect: str
    target: str
    at: str
    anchor_time: float
    start_frame: int
    start: float
    end: float


class Timeline(BaseModel):
    slide: str
    fps: int
    frames: int
    duration: float
    cues: list[TimelineCue]
    timeline: list[TimelineFrame]

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _r(v: float) -> float:
    return round(v, 4)


class EffectsRunner:
    """Overlay hook for :func:`deck2video.clock.render_frames`; records the timeline."""

    def __init__(self, page: SlidePage, cues: list[ScheduledCue], fps: int = FPS) -> None:
        self.page = page
        self.cues = cues
        self.fps = fps
        self.frames: list[TimelineFrame] = []
        self._last_signature: str | None = None
        for sc in cues:
            if isinstance(sc.resolved.cue, Reveal):
                sc.children = int(
                    page.evaluate(
                        """sel => [...document.querySelector('#d2v-camera > section')
                                  .querySelectorAll(sel)][0]?.children.length ?? 0""",
                        sc.resolved.cue.target,
                    )
                )

    def state(self, t: float) -> tuple[dict[str, Any], list[EffectFrame]]:
        camera: dict[str, Any] | None = None
        camera_start = -math.inf
        effects: list[dict[str, Any]] = []
        reveals: list[dict[str, Any]] = []
        frames: list[EffectFrame] = []
        for sc in self.cues:
            cue = sc.resolved.cue
            if isinstance(cue, Reveal):
                opacities = []
                for k in range(sc.children):
                    at = sc.start + k * cue.step
                    fade = min(cue.ease, cue.step) if cue.step > 0 else cue.ease
                    opacities.append(_r(smooth((t - at) / fade) if fade > 0 else float(t >= at)))
                reveals.append({"target": cue.target, "opacities": opacities})
                if t < sc.start:
                    phase: Phase = "pending"
                elif t < sc.end:
                    phase = "in"
                else:
                    phase = "done"
                progress = _r(sum(opacities) / len(opacities)) if opacities else 1.0
                if phase != "done":
                    frames.append(
                        EffectFrame(
                            cue=sc.resolved.index,
                            effect=cue.effect,
                            target=cue.target,
                            phase=phase,
                            progress=progress,
                        )
                    )
                continue
            phase, p = envelope(t, sc.start, cue.ease, cue.hold)
            if phase in ("pending", "done"):
                continue
            p = _r(p)
            frames.append(
                EffectFrame(
                    cue=sc.resolved.index,
                    effect=cue.effect,
                    target=cue.target,
                    phase=phase,
                    progress=p,
                )
            )
            if isinstance(cue, Zoom | Pan):
                if sc.start >= camera_start:
                    camera_start = sc.start
                    camera = self._camera(cue, sc, t, p)
                continue
            spec: dict[str, Any] = {
                "key": sc.key,
                "effect": cue.effect,
                "target": cue.target,
                "progress": p,
            }
            if isinstance(cue, Spotlight):
                spec.update(dim=cue.dim, padding=cue.padding)
            elif isinstance(cue, Outline):
                spec.update(color=cue.color, width=cue.width, padding=cue.padding)
            elif isinstance(cue, Pulse):
                period = max(0.5, cue.hold / cue.count) if cue.hold > 0 else 0.5
                since = t - sc.start - cue.ease
                wave = (
                    (since % period) / period
                    if phase == "hold" and since < period * cue.count
                    else 0.0
                )
                spec.update(color=cue.color, padding=cue.padding, wave=_r(wave))
            effects.append(spec)
        # every active effect reports its box, camera effects included
        for f in frames:
            if f.effect in ("zoom", "pan", "reveal"):
                effects.append(
                    {
                        "key": str(f.cue),
                        "effect": f.effect,
                        "target": f.target,
                        "progress": f.progress,
                    }
                )
        return {"camera": camera, "effects": effects, "reveals": reveals}, frames

    def _camera(self, cue: Zoom | Pan, sc: ScheduledCue, t: float, p: float) -> dict[str, Any]:
        scale = 1 + (cue.scale - 1) * p
        focus: list[float | None] = [None, None]
        if isinstance(cue, Pan):
            along = smooth((t - sc.start - cue.ease) / cue.hold) if cue.hold > 0 else 1.0
            axis = 0 if cue.direction == "horizontal" else 1
            focus[axis] = _r(along)
        return {"target": cue.target, "scale": _r(scale), "progress": p, "focus": focus}

    def __call__(self, i: int, t_ms: float) -> bool:
        t = t_ms / 1000
        state, frames = self.state(t)
        result = self.page.evaluate("s => window.__d2vfx.apply(s)", state)
        boxes = result["boxes"]
        for f in frames:
            box = boxes.get(str(f.cue))
            if box is not None:
                f.box = (box[0], box[1], box[2], box[3])
        frame = TimelineFrame(
            i=i, t=_r(t), camera=CameraFrame.model_validate(result["camera"]), effects=frames
        )
        self.frames.append(frame)
        signature = json.dumps([state, result], sort_keys=True)
        changed = signature != self._last_signature
        self._last_signature = signature
        return changed

    def timeline(self, slide_id: str, duration: float) -> Timeline:
        return Timeline(
            slide=slide_id,
            fps=self.fps,
            frames=len(self.frames),
            duration=_r(duration),
            cues=[
                TimelineCue(
                    cue=sc.resolved.index,
                    effect=sc.resolved.cue.effect,
                    target=sc.resolved.cue.target,
                    at=sc.resolved.cue.at,
                    anchor_time=_r(sc.anchor_time),
                    start_frame=sc.start_frame,
                    start=_r(sc.start),
                    end=_r(sc.end),
                )
                for sc in self.cues
            ],
            timeline=self.frames,
        )
