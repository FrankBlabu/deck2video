"""Virtual clock: deterministic frame-by-frame rendering of a slide.

Nothing runs in real time. Before every frame ``window.__d2v.step(t)`` (``js/clock.js``)
pauses every Web Animation (CSS animations and transitions, ``element.animate``) and every
SVG/SMIL timeline and sets it to the frame's time ``t = i * 1000 / fps`` ms. Animations
created later are timed from the virtual moment they first appear. ``performance.now``,
``Date``, ``requestAnimationFrame`` and timers follow the same clock; media elements are
paused and seeked. No sleeps, no screen recording.

Not virtualised: the GPU compositor's own timing (irrelevant once every animation is paused),
network (blocked), and workers (slides have none).

A frame is only captured when something can have changed: an animation or effect is
running, or was running on the previous frame. Otherwise the previous capture is reused,
which keeps long static narration cheap and is exact by construction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from importlib.resources import files

from pydantic import BaseModel

from deck2video.render import SlidePage

FPS = 30

CLOCK_JS = "\n".join(
    files("deck2video").joinpath(f"js/{name}.js").read_text(encoding="utf-8")
    for name in ("clock", "effects", "shapes")
)


def frame_time_ms(index: int, fps: int = FPS) -> float:
    return index * 1000 / fps


def frame_count(duration_s: float, fps: int = FPS) -> int:
    """Number of frames for ``duration_s`` seconds (rounded to the nearest frame)."""
    return max(1, round(duration_s * fps))


class StepState(BaseModel):
    changing: bool
    animations: int


class FrameStats(BaseModel):
    frames: int = 0
    captured: int = 0


OverlayHook = Callable[[int, float], bool]
"""Called after the clock moved to frame ``i`` at ``t`` ms; returns True if it changed the page."""


def step(page: SlidePage, t_ms: float) -> StepState:
    return StepState.model_validate(page.evaluate("t => window.__d2v.step(t)", t_ms))


def render_frames(
    page: SlidePage,
    n_frames: int,
    fps: int = FPS,
    overlay: OverlayHook | None = None,
    stats: FrameStats | None = None,
) -> Iterator[bytes]:
    """Yield one PNG per frame, ``n_frames`` frames from t=0."""
    stats = stats if stats is not None else FrameStats()
    last: bytes | None = None
    was_changing = True
    for i in range(n_frames):
        t = frame_time_ms(i, fps)
        state = step(page, t)
        changed = overlay(i, t) if overlay is not None else False
        if last is None or state.changing or was_changing or changed:
            last = page.screenshot()
            stats.captured += 1
        was_changing = state.changing
        stats.frames += 1
        yield last
