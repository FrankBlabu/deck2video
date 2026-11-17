from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deck2video.clock import FrameStats, frame_count, frame_time_ms, render_frames, step
from deck2video.deck import Deck, load_deck
from deck2video.fonts import FontCache
from deck2video.render import Renderer
from tests.conftest import DeckFactory, slide_html

pytestmark = pytest.mark.render

CSS = """
.fade { width: 200px; height: 200px; background: #c00;
  animation: fade 1000ms linear both; animation-delay: calc(var(--i, 0) * 200ms); }
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
"""
BODY = """
<div id="a" class="fade" style="--i:1"></div>
<svg width="200" height="200"><circle id="c" cx="100" cy="100" r="10" fill="#00c">
  <animate attributeName="r" from="10" to="50" dur="1s" begin="0s" fill="freeze"/>
</circle></svg>
"""


def anim_deck(make_deck: DeckFactory) -> Deck:
    return load_deck(make_deck({"s": slide_html("s", BODY, "Note.")}, css=CSS))


def test_frame_math() -> None:
    assert frame_time_ms(30) == 1000
    assert frame_time_ms(1) == pytest.approx(33.333, abs=0.001)
    assert frame_count(2.0) == 60
    assert frame_count(0.001) == 1


def test_css_and_svg_animations_follow_the_virtual_clock(
    make_deck: DeckFactory, tmp_path: Path
) -> None:
    deck = anim_deck(make_deck)
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        probe = """() => ({
            opacity: Number(getComputedStyle(document.getElementById('a')).opacity),
            current: document.getAnimations()[0].currentTime,
            r: document.getElementById('c').r.animVal.value,
            now: performance.now(),
            date: Date.now(),
        })"""
        samples = {}
        for frame in (0, 6, 15, 30, 45):
            step(page, frame_time_ms(frame))
            samples[frame] = page.evaluate(probe)
        page.close()
    # delay 200 ms, then 1000 ms linear fade
    assert samples[0]["opacity"] == 0
    assert samples[6]["opacity"] == 0  # 200 ms: fade starts now
    assert samples[15]["opacity"] == pytest.approx(0.3, abs=1e-3)  # 500 ms
    assert samples[30]["opacity"] == pytest.approx(0.8, abs=1e-3)  # 1000 ms
    assert samples[45]["opacity"] == 1
    for frame, s in samples.items():
        assert s["current"] == pytest.approx(frame_time_ms(frame), abs=1e-6)
        assert s["now"] == pytest.approx(frame_time_ms(frame), abs=1e-6)
    assert samples[0]["r"] == pytest.approx(10)
    assert samples[15]["r"] == pytest.approx(30, abs=0.01)
    assert samples[45]["r"] == pytest.approx(50)
    assert samples[30]["date"] - samples[0]["date"] == 1000


def test_frames_are_deterministic(make_deck: DeckFactory, tmp_path: Path) -> None:
    deck = anim_deck(make_deck)

    def hashes() -> list[str]:
        with Renderer(deck, FontCache(tmp_path)) as r:
            page = r.open(deck.slide("s"))
            out = [hashlib.sha256(f).hexdigest() for f in render_frames(page, 40)]
            page.close()
        return out

    first, second = hashes(), hashes()
    assert first == second
    assert len(set(first)) > 10  # the animation really moves


def test_static_frames_are_reused(make_deck: DeckFactory, tmp_path: Path) -> None:
    deck = load_deck(make_deck({"s": slide_html("s", "<p>Still</p>", "Note.")}))
    stats = FrameStats()
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        frames = list(render_frames(page, 90, stats=stats))
        page.close()
    assert stats.frames == 90
    assert stats.captured == 1
    assert len({hashlib.sha256(f).hexdigest() for f in frames}) == 1


def test_late_animations_start_at_their_virtual_creation_time(
    make_deck: DeckFactory, tmp_path: Path
) -> None:
    deck = load_deck(make_deck({"s": slide_html("s", "<div id='x'>x</div>", "Note.")}))
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        step(page, 500)
        page.evaluate(
            "() => { document.getElementById('x').animate([{opacity: 0}, {opacity: 1}], {duration: 1000}); setTimeout(() => { window.fired = performance.now(); }, 250); }"
        )
        step(page, 1000)
        state = page.evaluate("() => [document.getAnimations()[0].currentTime, window.fired]")
        page.close()
    assert state[0] == pytest.approx(500)
    assert state[1] == pytest.approx(750)
