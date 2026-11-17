from __future__ import annotations

from pathlib import Path

import pytest

from deck2video.deck import load_deck
from deck2video.fonts import FontCache
from deck2video.render import Renderer
from tests.conftest import DeckFactory, slide_html

pytestmark = pytest.mark.render

BODY = """
<div class="row" style="display:flex;gap:16px;align-items:center;width:600px">
  <x-shape id="arrow" kind="arrow-right" style="width:40px;height:20px;background:#5B6B86"></x-shape>
  <x-shape id="ring" kind="ellipse" style="width:80px;height:40px;background:#fff;border:3px solid #E07B28"></x-shape>
  <x-connector id="flow" head="end" style="flex:1;height:0;color:#8A97AB;border-width:3px"></x-connector>
</div>
<div id="host" style="position:relative;width:600px;height:300px">
  <x-connector id="pinned" x1="10" y1="20" x2="200" y2="120" route="hv" head="both"
    style="color:#123456;border-width:2px;border-style:dashed"></x-connector>
</div>
"""


def test_slides_elements_are_drawn(make_deck: DeckFactory, tmp_path: Path) -> None:
    deck = load_deck(make_deck({"s": slide_html("s", BODY, "Note.")}))
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("s"))
        info = page.evaluate(
            """() => {
              const shadow = (id) => document.getElementById(id).shadowRoot;
              const box = (id) => { const b = document.getElementById(id).getBoundingClientRect(); return [b.width, b.height]; };
              return {
                arrow: shadow('arrow').querySelector('polygon').getAttribute('points'),
                arrowFill: shadow('arrow').querySelector('polygon').getAttribute('fill'),
                arrowBg: getComputedStyle(document.getElementById('arrow')).backgroundColor,
                ring: shadow('ring').querySelector('ellipse').getAttribute('stroke'),
                flow: shadow('flow').querySelector('polyline').getAttribute('points'),
                flowBox: box('flow'),
                flowHeads: shadow('flow').querySelectorAll('polygon').length,
                pinned: shadow('pinned').querySelector('polyline').getAttribute('points'),
                pinnedDash: shadow('pinned').querySelector('polyline').getAttribute('stroke-dasharray'),
                pinnedHeads: shadow('pinned').querySelectorAll('polygon').length,
                pinnedColor: shadow('pinned').querySelector('polyline').getAttribute('stroke'),
              };
            }"""
        )
        page.close()
    assert info["arrow"] == "0,6 24,6 24,0 40,10 24,20 24,14 0,14"
    assert info["arrowFill"] == "rgb(91, 107, 134)"
    assert info["arrowBg"] == "rgba(0, 0, 0, 0)"
    assert info["ring"] == "rgb(224, 123, 40)"
    width = info["flowBox"][0]
    assert width > 100
    assert info["flow"] == f"0,0 {width},0"
    assert info["flowHeads"] == 1
    assert info["pinned"] == "10,20 200,20 200,120"
    assert info["pinnedDash"] == "6 4"
    assert info["pinnedHeads"] == 2
    assert info["pinnedColor"] == "rgb(18, 52, 86)"


def test_page_counter_follows_deck_position(make_deck: DeckFactory, tmp_path: Path) -> None:
    css = "section { counter-increment: page; }\n#n::after { content: counter(page); }"
    deck = load_deck(
        make_deck(
            {
                "a": slide_html("a", "<p id='n'></p>", "One."),
                "h": slide_html("h", "<p id='n'></p>", "Hidden.", " hidden"),
                "b": slide_html("b", "<p id='n'>x</p>", "Two."),
            },
            order=["a", "h", "b"],
            css=css,
        )
    )
    with Renderer(deck, FontCache(tmp_path)) as r:
        page = r.open(deck.slide("b"))
        reset = page.evaluate("() => document.getElementById('d2v-stage').style.counterReset")
        page.close()
    assert reset == "page 1"
