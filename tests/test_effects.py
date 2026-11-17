from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from deck2video.clock import FPS, frame_count, render_frames
from deck2video.cues import ResolvedCue, check_deck
from deck2video.deck import Deck, load_deck
from deck2video.effects import EffectsRunner, Timeline, envelope, schedule, smooth
from deck2video.fonts import FontCache
from deck2video.render import Renderer
from deck2video.tts import FakeAdapter, Narration, NarrationCache
from tests.conftest import DeckFactory, slide_html

BODY = """
<div class="row" style="display:flex;gap:40px">
  <svg width="600" height="400"><rect x="0" y="0" width="600" height="400" fill="#eee"/>
    <circle cx="300" cy="200" r="50" fill="#c00"/></svg>
  <ul class="steps" style="font-size:40px"><li>One</li><li>Two</li><li>Three</li></ul>
</div>
<p class="note">A note</p>
<p class="empty" style="display:none"></p>
"""
NOTE = (
    "Here is a chart. The red dot grows. Then three steps appear. "
    "Look at the note. Watch it pulse. And the chart outline. The end."
)
CUES = """
- {at: "The red dot", effect: zoom, target: "svg circle", scale: 2, hold: 1s, ease: 0.3s}
- {at: "three steps appear", effect: reveal, target: "ul.steps", step: 0.5s, ease: 0.2s}
- {at: "Look at the note", effect: spotlight, target: "p.note", hold: 0.5s, ease: 0.2s}
- {at: "Watch it pulse", effect: pulse, target: "p.note", hold: 1s, count: 2, ease: 0.1s}
- {at: "the chart outline", effect: outline, target: "svg", hold: 0.5s, ease: 0.2s}
"""


def test_envelope() -> None:
    assert envelope(0.9, 1, 0.5, 1) == ("pending", 0.0)
    assert envelope(1.25, 1, 0.5, 1) == ("in", 0.5)
    assert envelope(2.0, 1, 0.5, 1) == ("hold", 1.0)
    assert envelope(2.75, 1, 0.5, 1) == ("out", 0.5)
    assert envelope(3.0, 1, 0.5, 1) == ("done", 0.0)
    assert smooth(-1) == 0
    assert smooth(2) == 1


class Setup(NamedTuple):
    deck: Deck
    cues: list[ResolvedCue]
    narration: Narration


@pytest.fixture
def setup(make_deck: DeckFactory, tmp_path: Path) -> Setup:
    deck = load_deck(make_deck({"s": slide_html("s", BODY, NOTE)}, cues={"s": CUES}))
    cues, problems = check_deck(deck)
    assert problems == []
    narration, _ = NarrationCache(tmp_path / "tts").narrate(FakeAdapter(), deck.slide("s").note)
    return Setup(deck, cues["s"], narration)


def test_cues_start_within_one_frame_of_their_anchor_word(setup: Setup) -> None:
    _, cues, narration = setup
    scheduled = schedule(cues, narration.words)
    assert len(scheduled) == 5
    for sc in scheduled:
        word = narration.words[sc.resolved.word_start]
        assert sc.anchor_time == word.start
        assert abs(sc.start - word.start) <= 1 / FPS
        assert sc.start_frame == round(word.start * FPS)


@pytest.mark.render
def test_effects_render_and_timeline(setup: Setup, tmp_path: Path) -> None:
    deck, cues, narration = setup
    scheduled = schedule(cues, narration.words)
    n = frame_count(narration.duration)
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        runner = EffectsRunner(page, scheduled)
        frames = list(render_frames(page, n, overlay=runner))
        timeline = runner.timeline("s", narration.duration)
        page.close()
    assert len(frames) == n == timeline.frames

    zoom = next(c for c in timeline.cues if c.effect == "zoom")
    base = timeline.timeline[0]
    assert base.camera.scale == 1
    # hold frame of the zoom: camera at scale 2, target box exactly twice as large
    hold_frame = zoom.start_frame + round(0.3 * FPS) + 5
    f = timeline.timeline[hold_frame]
    assert f.camera.scale == 2
    z = next(e for e in f.effects if e.effect == "zoom")
    assert z.phase == "hold"
    assert z.box is not None
    assert z.box[2] == pytest.approx(200, abs=0.5)  # r=50 -> 100 px wide, x2
    assert z.box[3] == pytest.approx(200, abs=0.5)
    # zoom keeps the slide covering the frame
    assert f.camera.tx <= 0
    assert f.camera.ty <= 0
    # at the cue's start frame the zoom is active, one frame before it is not
    assert any(e.effect == "zoom" for e in timeline.timeline[zoom.start_frame].effects)
    assert not any(e.effect == "zoom" for e in timeline.timeline[zoom.start_frame - 1].effects)
    after = timeline.timeline[zoom.start_frame + round(1.6 * FPS) + 2]
    assert after.camera.scale == 1

    reveal = next(c for c in timeline.cues if c.effect == "reveal")
    assert reveal.end == pytest.approx(reveal.start + 2 * 0.5 + 0.2, abs=1e-3)

    for effect in ("spotlight", "pulse", "outline"):
        cue = next(c for c in timeline.cues if c.effect == effect)
        mid = timeline.timeline[cue.start_frame + round(0.25 * FPS)]
        hit = next(e for e in mid.effects if e.effect == effect)
        assert hit.box is not None
        assert hit.box[2] > 0

    out = tmp_path / "build" / "s" / "timeline.json"
    timeline.write(out)
    again = Timeline.model_validate(json.loads(out.read_text()))
    assert again == timeline


@pytest.mark.render
def test_reveal_hides_children_until_their_turn(setup: Setup, tmp_path: Path) -> None:
    deck, cues, narration = setup
    scheduled = schedule(cues, narration.words)
    reveal = next(sc for sc in scheduled if sc.resolved.cue.effect == "reveal")
    opacity = "() => [...document.querySelectorAll('ul.steps li')].map(li => Number(getComputedStyle(li).opacity))"
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        runner = EffectsRunner(page, scheduled)
        seen = {}
        for i, _ in enumerate(render_frames(page, reveal.start_frame + 40, overlay=runner)):
            if i in (0, reveal.start_frame + 7, reveal.start_frame + 22, reveal.start_frame + 39):
                seen[i] = page.evaluate(opacity)
        source = page.evaluate("() => document.querySelector('ul.steps').outerHTML")
        page.close()
    assert seen[0] == [0, 0, 0]
    assert seen[reveal.start_frame + 7] == [1, 0, 0]
    assert seen[reveal.start_frame + 22] == [1, 1, 0]
    assert seen[reveal.start_frame + 39] == [1, 1, 1]
    assert "opacity" in source  # applied at render time only
    assert "opacity" not in deck.slide("s").html


@pytest.mark.render
def test_target_without_size_is_an_error(make_deck: DeckFactory, tmp_path: Path) -> None:
    deck = load_deck(
        make_deck(
            {"s": slide_html("s", BODY, NOTE)},
            cues={"s": '- {at: "Look at the note", effect: outline, target: "p.empty"}'},
        )
    )
    cues, problems = check_deck(deck)
    assert problems == []
    narration, _ = NarrationCache(tmp_path / "tts").narrate(FakeAdapter(), deck.slide("s").note)
    scheduled = schedule(cues["s"], narration.words)
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        runner = EffectsRunner(page, scheduled)
        with pytest.raises(Exception, match=r"target \\?\"p.empty\\?\" has no size"):
            list(render_frames(page, frame_count(narration.duration), overlay=runner))
        page.close()
