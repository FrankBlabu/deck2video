"""End-to-end: the two-slide fixture deck in tests/fixtures/deck builds to a real MP4."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from deck2video.assemble import Pad, segment_timing
from deck2video.build import BuildOptions, BuildReport, build
from deck2video.cli import main
from deck2video.clock import FPS
from deck2video.deck import load_deck
from deck2video.effects import Timeline
from deck2video.media import probe
from deck2video.subtitles import captions
from deck2video.tts import FakeAdapter, NarrationCache

pytestmark = [pytest.mark.slow, pytest.mark.render]

FIXTURE = Path(__file__).parent / "fixtures" / "deck"


def run(tmp_path: Path, name: str, cache: Path) -> BuildReport:
    return build(
        BuildOptions(
            deck=FIXTURE,
            output=tmp_path / name / "talk.mp4",
            build_dir=tmp_path / name / "build",
            adapter="fake",
            cache_root=cache,
        ),
        log=lambda _: None,
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, BuildReport, BuildReport]:
    tmp = tmp_path_factory.mktemp("integration")
    before = _hashes(FIXTURE)
    first = run(tmp, "a", tmp / "cache")
    second = run(tmp, "b", tmp / "cache")
    assert _hashes(FIXTURE) == before, "the deck must never be modified"
    return tmp, first, second


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_fixture_deck_checks_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(FIXTURE)]) == 0
    assert "ok: 2 slide(s), 6 cue(s)" in capsys.readouterr().out


def test_mp4_duration_streams_and_subtitles(
    built: tuple[Path, BuildReport, BuildReport],
) -> None:
    tmp, report, _ = built
    deck = load_deck(FIXTURE)
    narrations = NarrationCache(tmp / "cache" / "tts")
    expected = 0.0
    expected_captions = 0
    for slide in deck.slides:
        narration, cached = narrations.narrate(FakeAdapter(), slide.note)
        assert cached
        timing = segment_timing(narration.duration, Pad())
        assert timing.duration == pytest.approx(narration.duration + Pad().total, abs=1 / FPS)
        expected += timing.duration
        expected_captions += len(captions(narration.words))
    assert report.duration == pytest.approx(expected)

    info = probe(report.output)
    streams = {s["codec_type"]: s for s in info["streams"]}
    assert set(streams) == {"video", "audio"}
    assert streams["video"]["codec_name"] == "h264"
    assert (streams["video"]["width"], streams["video"]["height"]) == (1920, 1080)
    assert streams["video"]["r_frame_rate"] == "30/1"
    assert streams["audio"]["codec_name"] == "aac"
    assert float(info["format"]["duration"]) == pytest.approx(expected, abs=0.05)
    assert int(streams["video"]["nb_frames"]) == round(expected * FPS)
    assert report.srt.read_text().count(" --> ") == expected_captions
    assert report.vtt.read_text().count(" --> ") == expected_captions


def test_timeline_shows_zoom_box_scaled_at_cue_frame(
    built: tuple[Path, BuildReport, BuildReport],
) -> None:
    tmp, _, _ = built
    timeline = Timeline.model_validate(
        json.loads((tmp / "a" / "build" / "chart" / "timeline.json").read_text())
    )
    zoom = next(c for c in timeline.cues if c.effect == "zoom")
    assert abs(zoom.start - zoom.anchor_time) <= 1 / FPS
    before = timeline.timeline[zoom.start_frame - 1]
    assert not any(e.effect == "zoom" for e in before.effects)
    base = next(
        e for f in timeline.timeline for e in f.effects if e.effect == "zoom" and e.progress == 0
    )
    hold = next(
        f
        for f in timeline.timeline
        if any(e.effect == "zoom" and e.phase == "hold" for e in f.effects)
    )
    box = next(e for e in hold.effects if e.effect == "zoom").box
    assert base.box is not None
    assert box is not None
    assert hold.camera.scale == pytest.approx(1.8)
    assert box[2] == pytest.approx(base.box[2] * 1.8, rel=1e-3)
    assert box[3] == pytest.approx(base.box[3] * 1.8, rel=1e-3)


def test_two_builds_are_identical(built: tuple[Path, BuildReport, BuildReport]) -> None:
    tmp, first, second = built
    assert first.rendered == second.rendered == ["chart", "steps"]
    for slide in ("chart", "steps"):
        for name in ("timeline.json", "frames.sha256"):
            a = (tmp / "a" / "build" / slide / name).read_bytes()
            b = (tmp / "b" / "build" / slide / name).read_bytes()
            assert a == b, f"{slide}/{name} differs between builds"
