from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deck2video.deck import load_deck
from deck2video.fonts import FontCache, FontError
from deck2video.render import Renderer
from tests.conftest import DeckFactory, slide_html
from tests.helpers import StubFetch, png, system_font

FACE = {"t": {"family": "Test Face", "href": "https://fonts.googleapis.com/css2?family=Test+Face"}}


def test_font_cache_fetches_once_and_rewrites_urls(make_deck: DeckFactory, tmp_path: Path) -> None:
    deck = load_deck(make_deck({"a": slide_html("a", "", "Note.")}, faces=FACE))
    fetch = StubFetch("Test Face", b"FONTDATA")
    cache = FontCache(tmp_path / "cache", fetch=fetch)
    css = cache.deck_css(deck)
    assert "fonts.gstatic.com" not in css
    assert "url(/_fonts/" in css
    assert len(fetch.calls) == 2
    assert cache.deck_css(deck) == css
    assert len(fetch.calls) == 2
    url = css.split("url(")[1].split(")")[0]
    resolved = cache.resolve(url)
    assert resolved is not None
    assert resolved.read_bytes() == b"FONTDATA"
    assert cache.resolve("/_fonts/../../etc/passwd") is None


def test_font_fetch_failure_is_reported(make_deck: DeckFactory, tmp_path: Path) -> None:
    deck = load_deck(make_deck({"a": slide_html("a", "", "Note.")}, faces=FACE))

    def offline(url: str) -> bytes:
        raise OSError("network down")

    with pytest.raises(FontError, match="could not fetch font 'Test Face'"):
        FontCache(tmp_path, fetch=offline).deck_css(deck)


@pytest.mark.render
def test_slide_renders_with_local_font_and_blocks_network(
    make_deck: DeckFactory, tmp_path: Path
) -> None:
    font = system_font()
    if font is None:
        pytest.skip("no system TTF to stand in for a web font")
    body = (
        "<p id='t' style=\"font-family:'Test Face'\">Hello</p>"
        "<img id='local' src='/_blob/pic' alt=''>"
        "<img id='remote' src='https://example.com/x.png' alt=''>"
    )
    root = make_deck(
        {"a": slide_html("a", body, "Note.")},
        faces=FACE,
        css="@import url('https://fonts.googleapis.com/css2?family=Other');\nsection { color: #123; }",
        assets={"pic": "pic.png"},
        files={"pic.png": png()},
    )
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    deck = load_deck(root)
    with Renderer(deck, FontCache(tmp_path / "cache", fetch=StubFetch("Test Face", font))) as r:
        page = r.open(deck.slide("a"))
        state = page.evaluate(
            """() => ({
                loaded: [...document.fonts].filter(f => f.family.includes('Test Face')).map(f => f.status),
                check: document.fonts.check("32px 'Test Face'"),
                local: document.getElementById('local').naturalWidth,
                remote: document.getElementById('remote').naturalWidth,
                section: [document.querySelector('section').offsetWidth, document.querySelector('section').offsetHeight],
                aside: getComputedStyle(document.querySelector('aside')).display,
            })"""
        )
        blocked = list(page.blocked)
        page.close()
    assert state["loaded"] == ["loaded"]
    assert state["check"] is True
    assert state["local"] == 4
    assert state["remote"] == 0
    assert state["section"] == [1920, 1080]
    assert state["aside"] == "none"
    assert "https://example.com/x.png" in blocked
    assert any(u.startswith("https://fonts.googleapis.com/") for u in blocked)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    assert before == after
