from __future__ import annotations

from pathlib import Path

import pytest

import deck2video.build as build_module
from deck2video.build import BuildOptions, build
from deck2video.cli import main
from deck2video.problems import DeckError
from tests.conftest import DeckFactory, slide_html

pytestmark = [pytest.mark.slow, pytest.mark.render]

FADE = ' data-transition="fade"'


@pytest.fixture
def deck(make_deck: DeckFactory) -> Path:
    return make_deck(
        {
            "one": slide_html("one", "<h2 class='t'>One</h2>", "First slide here.", FADE),
            "two": slide_html("two", "<h2 class='t'>Two</h2>", "Second slide here.", FADE),
            "three": slide_html("three", "<h2 class='t'>Three</h2>", "Third one."),
        },
        cues={"two": '- {at: "Second slide", effect: outline, target: "h2.t", hold: 0.3s}'},
    )


def opts(deck: Path, tmp_path: Path, **kw: object) -> BuildOptions:
    return BuildOptions(
        deck=deck,
        output=tmp_path / "out" / "talk.mp4",
        build_dir=tmp_path / "build",
        adapter="fake",
        cache_root=tmp_path / "cache",
        **kw,  # type: ignore[arg-type]
    )


def test_incremental_build(deck: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    first = build(opts(deck, tmp_path), log=lines.append)
    assert first.rendered == ["one", "two", "three"]
    assert first.output.is_file()
    assert first.srt.read_text().count(" --> ") == 3
    for sid in ("one", "two", "three"):
        assert (tmp_path / "build" / sid / "timeline.json").is_file()
    assert any("[2/3] two: rendered" in line for line in lines)

    second = build(opts(deck, tmp_path))
    assert second.rendered == []
    assert second.duration == first.duration

    note = deck / "project" / "slides" / "two.html"
    note.write_text(note.read_text().replace("Second slide here.", "Second slide, edited."))
    third = build(opts(deck, tmp_path))
    assert third.rendered == ["two"]

    monkeypatch.setattr(build_module, "__version__", "99.0")
    fourth = build(opts(deck, tmp_path))
    assert fourth.rendered == ["one", "two", "three"]


def test_slide_subset(deck: Path, tmp_path: Path) -> None:
    report = build(opts(deck, tmp_path, slides=["three", "one"]))
    assert [s.id for s in report.slides] == ["one", "three"]


def test_build_refuses_invalid_cues(deck: Path, tmp_path: Path) -> None:
    (deck / "cues" / "two.yaml").write_text('- {at: "nowhere", effect: zoom, target: "h2.t"}')
    with pytest.raises(DeckError, match="anchor phrase does not occur"):
        build(opts(deck, tmp_path))


def test_build_command(deck: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK2VIDEO_CACHE", str(tmp_path / "cache"))
    out = tmp_path / "cli.mp4"
    args = ["build", str(deck), "-o", str(out), "--voice-adapter", "fake"]
    assert main([*args, "--build-dir", str(tmp_path / "b")]) == 0
    assert out.is_file()
    assert out.with_suffix(".vtt").is_file()
    assert main([*args, "--slides", "nope", "--build-dir", str(tmp_path / "b")]) == 1
