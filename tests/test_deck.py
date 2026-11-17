from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deck2video.deck import load_deck, parse_deck
from deck2video.problems import DeckError
from deck2video.text import find_phrase, norm_word, phrase_span, words
from tests.conftest import DeckFactory, slide_html


def messages(root: Path) -> list[str]:
    _, problems = parse_deck(root)
    return [str(p) for p in problems]


def test_order_notes_and_assets(make_deck: DeckFactory) -> None:
    root = make_deck(
        {
            "b": slide_html("b", '<img src="/_blob/pic1" alt="">', "Second   slide\n note."),
            "a": slide_html("a", "<p>Hi</p>", "First &amp; foremost.", ' data-transition="fade"'),
            "c": slide_html("c", "<p>skip</p>", "Hidden.", " hidden"),
        },
        order=["a", "b", "c"],
        assets={"pic1": "img/pic.png"},
        files={"img/pic.png": b"\x89PNG"},
        faces={"x": {"family": "Lora", "href": "https://fonts.googleapis.com/css2?family=Lora"}},
    )
    deck = load_deck(root)
    assert [s.id for s in deck.slides] == ["a", "b", "c"]
    assert [s.id for s in deck.visible_slides] == ["a", "b"]
    assert deck.slide("a").note == "First & foremost."
    assert deck.slide("a").transition == "fade"
    assert deck.slide("b").note == "Second slide note."
    assert deck.slide("b").blobs == ("pic1",)
    assert deck.assets["pic1"] == (root / "img" / "pic.png").resolve()
    assert deck.faces[0].family == "Lora"


def test_faces_as_list(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", "", "Note.")}, faces=["Inter", "Lora"])
    assert [f.family for f in load_deck(root).faces] == ["Inter", "Lora"]


def test_deck_files_are_not_modified(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", "<p>x</p>", "Note.")})
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    load_deck(root)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_missing_index(tmp_path: Path) -> None:
    assert messages(tmp_path) == [f"{tmp_path.resolve()}: project/deck.json not found"]


def test_missing_slide_file(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", "", "Note.")}, order=["a", "zz"])
    assert messages(root) == ["slides/zz.html: slide file not found"]


def test_section_id_mismatch(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("other", "", "Note.")})
    assert messages(root) == ["slides/a.html: <section id=\"other\"> does not match slide id 'a'"]


def test_missing_aside(make_deck: DeckFactory) -> None:
    root = make_deck({"a": '<section id="a"><p>x</p></section>'})
    assert messages(root) == ["slides/a.html: no <aside> narration"]


def test_empty_aside(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", "", "   ")})
    assert messages(root) == ["slides/a.html: <aside> narration is empty"]


def test_aside_not_last(make_deck: DeckFactory) -> None:
    root = make_deck({"a": '<section id="a"><aside>Note.</aside><p>x</p></section>'})
    assert messages(root) == ["slides/a.html: <aside> must be the last child of <section>"]


def test_unresolved_blob(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", '<img src="/_blob/nope" alt="">', "Note.")})
    assert messages(root) == ["slides/a.html: /_blob/nope is not listed in assets.json"]


def test_asset_file_missing(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", "", "Note.")}, assets={"p": "missing.png"})
    assert messages(root) == ["assets.json: asset 'p': file 'missing.png' not found"]


def test_duplicate_order(make_deck: DeckFactory) -> None:
    root = make_deck({"a": slide_html("a", "", "Note.")}, order=["a", "a"])
    assert messages(root) == ["deck.json: slide id 'a' appears twice in 'order'"]


def test_load_deck_raises_all_problems(make_deck: DeckFactory) -> None:
    root = make_deck({"a": '<section id="a"></section>'}, order=["a", "b"])
    with pytest.raises(DeckError) as exc:
        load_deck(root)
    assert len(exc.value.problems) == 2


def test_words_and_phrases() -> None:
    note = "Take one part – a function. It’s “nearly two thirds” at a thousand!"
    ws = words(note)
    assert ws[2] == "part"
    assert norm_word("“nearly") == "nearly"
    assert norm_word("It’s") == "it's"
    assert find_phrase(ws, "nearly two thirds at a thousand") == [7]
    assert find_phrase(ws, "part a function") == [2]
    assert phrase_span(ws, 2, "part a function") == (2, 6)
    assert find_phrase(ws, "absent words") == []
