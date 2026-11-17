from __future__ import annotations

from pathlib import Path

import pytest

from deck2video.cli import main
from deck2video.cues import Pan, Reveal, Zoom, check_deck, parse_duration
from deck2video.deck import load_deck
from tests.conftest import DeckFactory, slide_html

BODY = """
<div class="chart"><svg width="100" height="100">
  <circle cx="1" cy="1" r="1"/><circle cx="2" cy="2" r="1"/>
  <polyline points="0,0 1,1"/>
</svg></div>
<ul class="steps"><li>One</li><li>Two</li><li>Three</li></ul>
<p class="empty"></p>
"""
NOTE = (
    "The curve rises fast. The dashed line stays flat. Three steps follow. The curve rises again."
)


def deck_with(make_deck: DeckFactory, cues: str) -> Path:
    return make_deck({"s": slide_html("s", BODY, NOTE)}, cues={"s": cues})


def problems(make_deck: DeckFactory, cues: str) -> list[str]:
    deck = load_deck(deck_with(make_deck, cues))
    _, found = check_deck(deck)
    return [str(p) for p in found]


def test_valid_cues_resolve_to_words(make_deck: DeckFactory) -> None:
    deck = load_deck(
        deck_with(
            make_deck,
            """
- at: "Three steps follow"
  effect: reveal
  target: "ul.steps"
- at: "The dashed line"
  effect: zoom
  target: "svg polyline:nth-of-type(1)"
  scale: 1.8
  hold: 2.5s
- at: "dashed line stays"
  effect: pan
  target: ".chart"
  ease: 300ms
""",
        )
    )
    cues, found = check_deck(deck)
    assert found == []
    resolved = cues["s"]
    assert [r.cue.effect for r in resolved] == ["zoom", "pan", "reveal"]
    zoom = resolved[0]
    assert isinstance(zoom.cue, Zoom)
    assert (zoom.word_start, zoom.word_end) == (4, 7)
    assert zoom.cue.scale == 1.8
    assert zoom.cue.hold == 2.5
    assert isinstance(resolved[1].cue, Pan)
    assert resolved[1].cue.ease == pytest.approx(0.3)
    assert isinstance(resolved[2].cue, Reveal)


def test_anchor_missing(make_deck: DeckFactory) -> None:
    assert problems(make_deck, '- {at: "purple cow", effect: outline, target: "ul.steps"}') == [
        "cues/s.yaml #1 (at 'purple cow'): anchor phrase does not occur in the note"
    ]


def test_anchor_twice(make_deck: DeckFactory) -> None:
    assert problems(
        make_deck, '- {at: "The curve rises", effect: outline, target: "ul.steps"}'
    ) == [
        "cues/s.yaml #1 (at 'The curve rises'): anchor phrase occurs 2 times in the note; make it unique"
    ]


def test_selector_matches_none(make_deck: DeckFactory) -> None:
    assert problems(make_deck, '- {at: "Three steps", effect: outline, target: "svg rect"}') == [
        "cues/s.yaml #1 (at 'Three steps'): target 'svg rect' matches 0 elements on the slide; it must match exactly one"
    ]


def test_selector_matches_many(make_deck: DeckFactory) -> None:
    assert problems(
        make_deck, '- {at: "Three steps", effect: spotlight, target: "svg circle"}'
    ) == [
        "cues/s.yaml #1 (at 'Three steps'): target 'svg circle' matches 2 elements on the slide; it must match exactly one"
    ]


def test_selector_does_not_reach_narration(make_deck: DeckFactory) -> None:
    assert problems(make_deck, '- {at: "Three steps", effect: outline, target: "aside"}') == [
        "cues/s.yaml #1 (at 'Three steps'): target 'aside' matches 0 elements on the slide; it must match exactly one"
    ]


def test_invalid_selector(make_deck: DeckFactory) -> None:
    assert problems(make_deck, '- {at: "Three steps", effect: outline, target: "ul >> li"}') == [
        "cues/s.yaml #1 (at 'Three steps'): invalid CSS selector 'ul >> li'"
    ]


def test_unknown_effect(make_deck: DeckFactory) -> None:
    assert problems(make_deck, '- {at: "Three steps", effect: wobble, target: "ul.steps"}') == [
        "cues/s.yaml #1 (at 'Three steps'): effect 'wobble' is not one of zoom, pan, spotlight, outline, pulse, reveal"
    ]


def test_unknown_key_and_bad_values(make_deck: DeckFactory) -> None:
    found = problems(
        make_deck,
        '- {at: "Three steps", effect: zoom, target: "ul.steps", scale: 0.5, colour: red, hold: soon}',
    )
    assert found == [
        "cues/s.yaml #1 (at 'Three steps'): hold: 'soon' is not a duration such as '2.5s' or '500ms'",
        "cues/s.yaml #1 (at 'Three steps'): scale: Input should be greater than 1",
        "cues/s.yaml #1 (at 'Three steps'): colour: unknown key",
    ]


def test_reveal_needs_children(make_deck: DeckFactory) -> None:
    assert problems(make_deck, '- {at: "Three steps", effect: reveal, target: "p.empty"}') == [
        "cues/s.yaml #1 (at 'Three steps'): reveal target 'p.empty' has no child elements"
    ]


def test_not_a_list(make_deck: DeckFactory) -> None:
    assert problems(make_deck, "at: x") == ["cues/s.yaml: must be a list of cues"]


def test_stray_cue_file(make_deck: DeckFactory) -> None:
    root = make_deck({"s": slide_html("s", BODY, NOTE)}, cues={"ghost": "[]"})
    _, found = check_deck(load_deck(root))
    assert [str(p) for p in found] == ["cues/ghost.yaml: no slide 'ghost' in the deck"]


def test_durations() -> None:
    assert parse_duration("2.5s") == 2.5
    assert parse_duration("500ms") == 0.5
    assert parse_duration(3) == 3.0
    with pytest.raises(ValueError, match="not a duration"):
        parse_duration("2 minutes")


def test_check_command(make_deck: DeckFactory, capsys: pytest.CaptureFixture[str]) -> None:
    good = deck_with(make_deck, '- {at: "Three steps", effect: reveal, target: "ul.steps"}')
    assert main(["check", str(good)]) == 0
    assert "ok: 1 slide(s), 1 cue(s)" in capsys.readouterr().out
    (good / "cues" / "s.yaml").write_text(
        '- {at: "nope", effect: wobble, target: "x"}\n- {at: "nope", effect: zoom, target: "x"}\n'
    )
    assert main(["check", str(good)]) == 1
    err = capsys.readouterr().err
    assert "effect 'wobble'" in err
    assert "anchor phrase does not occur" in err
    assert "3 problem(s) found" in err


def test_check_unknown_slide_selection(
    make_deck: DeckFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_deck({"s": slide_html("s", BODY, NOTE)})
    assert main(["check", str(root), "--slides", "s,zz"]) == 1
    assert "--slides: no slide 'zz' in the deck" in capsys.readouterr().err
