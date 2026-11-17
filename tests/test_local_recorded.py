from __future__ import annotations

import itertools
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from deck2video.build import BuildOptions, build
from deck2video.problems import DeckError
from deck2video.text import words
from deck2video.tts import (
    FakeAdapter,
    LocalAdapter,
    NarrationCache,
    RecordedAdapter,
    TTSError,
    get_adapter,
)
from deck2video.tts.align import Heard, align, merge_pieces
from tests.conftest import DeckFactory, slide_html


def heard(*items: tuple[str, float, float]) -> list[Heard]:
    return [Heard(text=t, start=s, end=e) for t, s, e in items]


def test_align_exact_match() -> None:
    got = align(
        "Hello there, world.",
        heard((" Hello", 0.1, 0.4), (" there,", 0.5, 0.8), (" world.", 0.9, 1.3)),
        2.0,
    )
    assert [(w.text, w.start, w.end) for w in got] == [
        ("Hello", 0.1, 0.4),
        ("there,", 0.5, 0.8),
        ("world.", 0.9, 1.3),
    ]


def test_align_merges_pieces_and_ignores_extra_words() -> None:
    rec = heard(
        (" fails", 1.0, 1.2), (" with", 1.2, 1.4), (" 0", 1.4, 1.6), (".1", 1.6, 1.9),
        (" percent.", 1.9, 2.3), (" And", 2.3, 2.3), (" then", 2.3, 2.3),
    )  # fmt: skip
    assert [h.text for h in merge_pieces(rec)][:3] == ["fails", "with", "0.1"]
    got = align("fails with 0.1 percent.", rec, 3.0)
    assert [(w.text, w.start, w.end) for w in got] == [
        ("fails", 1.0, 1.2),
        ("with", 1.2, 1.4),
        ("0.1", 1.4, 1.9),
        ("percent.", 1.9, 2.3),
    ]


def test_align_shares_time_for_differently_written_words() -> None:
    got = align(
        "at a thousand parts",
        heard((" at", 0.0, 0.2), (" 1,000", 0.2, 1.0), (" parts", 1.0, 1.4)),
        2.0,
    )
    assert [w.text for w in got] == ["at", "a", "thousand", "parts"]
    assert got[1].start == pytest.approx(0.2)
    assert got[2].end == pytest.approx(1.0)
    assert got[1].end == pytest.approx(got[2].start)
    assert got[3].start == 1.0


def test_align_fills_missed_words_from_the_gap() -> None:
    got = align("one two three four", heard((" one", 0.0, 0.5), (" four", 2.0, 2.4)), 3.0)
    assert [w.start for w in got] == [0.0, 0.5, pytest.approx(0.5 + 1.5 * 3 / 8, abs=1e-3), 2.0]
    assert all(a.start <= b.start for a, b in itertools.pairwise(got))


def test_align_with_nothing_heard_spreads_over_the_audio() -> None:
    got = align("a b", [], 2.0)
    assert [(w.start, w.end) for w in got] == [(0.0, 1.0), (1.0, 2.0)]


class EchoRecognizer:
    """Hears exactly the note: one word every 0.4 s."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def identity(self) -> dict[str, Any]:
        return {"recognizer": "echo"}

    def hear(self, wav: Path) -> list[Heard]:
        self.calls += 1
        return [
            Heard(text=" " + w, start=i * 0.4, end=i * 0.4 + 0.3)
            for i, w in enumerate(words(self.text))
        ]


def fake_wav(text: str) -> bytes:
    return FakeAdapter().synthesize(text).audio


NOTE = "Recorded words land where they were spoken."


def test_recorded_adapter_aligns_the_slide_recording(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    (rec / "intro.wav").write_bytes(fake_wav(NOTE))
    recognizer = EchoRecognizer(NOTE)
    adapter = RecordedAdapter(rec, recognizer=recognizer)
    cache = NarrationCache(tmp_path / "cache")
    narration, cached = cache.narrate(adapter, NOTE, "intro")
    assert not cached
    assert [w.text for w in narration.words] == words(NOTE)
    assert narration.words[2].start == pytest.approx(0.8)
    assert cache.narrate(adapter, NOTE, "intro")[1] is True
    # a new recording is a cache miss
    (rec / "intro.wav").write_bytes(fake_wav(NOTE + " Again."))
    assert cache.narrate(adapter, NOTE, "intro")[1] is False
    assert recognizer.calls == 2


def test_recorded_adapter_reports_missing_and_ambiguous_recordings(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    (rec / "a.wav").write_bytes(b"x")
    (rec / "a.mp3").write_bytes(b"x")
    adapter = RecordedAdapter(rec, recognizer=EchoRecognizer(NOTE))
    problems = [str(p) for p in adapter.problems(["a", "b"])]
    assert problems[0].startswith(
        "recordings/a: more than one recording for slide 'a': a.wav, a.mp3"
    )
    assert problems[1] == f"recordings/b: no recording for slide 'b': expected {rec / 'b'}.wav"
    with pytest.raises(TTSError, match="needs to know which slide"):
        adapter.synthesize(NOTE)


def test_local_adapter_aligns_synthesised_audio(tmp_path: Path) -> None:
    adapter = LocalAdapter("en_US-test", recognizer=EchoRecognizer(NOTE), synth=fake_wav)
    narration, _ = NarrationCache(tmp_path).narrate(adapter, NOTE)
    assert [w.text for w in narration.words] == words(NOTE)
    assert adapter.identity()["voice"] == "en_US-test"
    assert get_adapter("local").identity()["voice"] == "en_US-lessac-medium"


@pytest.mark.slow
@pytest.mark.render
def test_build_with_recordings(
    make_deck: DeckFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = make_deck(
        {"intro": slide_html("intro", "<h2 id='t'>Hi</h2>", NOTE)},
        cues={
            "intro": '- {at: "where they were spoken", effect: outline, target: "#t", hold: 0.3s}'
        },
    )
    opts = BuildOptions(
        deck=deck, output=tmp_path / "talk.mp4", build_dir=tmp_path / "build",
        adapter="recorded", cache_root=tmp_path / "cache",
    )  # fmt: skip
    with pytest.raises(DeckError, match="no recording for slide 'intro'"):
        build(opts, log=lambda _: None)
    (deck / "recordings").mkdir()
    (deck / "recordings" / "intro.wav").write_bytes(fake_wav(NOTE))
    monkeypatch.setattr(
        "deck2video.tts.align.WhisperRecognizer.hear",
        lambda self, wav: EchoRecognizer(NOTE).hear(wav),
    )
    report = build(opts, log=lambda _: None)
    assert report.rendered == ["intro"]
    assert report.output.is_file()


LOCAL_MODELS = os.environ.get("DECK2VIDEO_TEST_LOCAL_MODELS") == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not LOCAL_MODELS, reason="set DECK2VIDEO_TEST_LOCAL_MODELS=1 (downloads Piper + Whisper)"
)
def test_local_voice_end_to_end(tmp_path: Path) -> None:
    cache_root = Path(os.environ.get("DECK2VIDEO_CACHE", tmp_path))
    text = "Take one part, a function, that fails with a probability of 0.1 percent."
    adapter = LocalAdapter(cache_root=cache_root)
    narration, _ = NarrationCache(tmp_path / "tts").narrate(adapter, text)
    assert [w.text for w in narration.words] == words(text)
    starts = [w.start for w in narration.words]
    assert starts == sorted(starts)
    assert narration.words[-1].end <= narration.duration + 0.05
    assert narration.words[0].start < 1.0
    # the same audio, read back as a recording, aligns to the same words
    rec = tmp_path / "rec"
    rec.mkdir()
    shutil.copy(narration.audio, rec / "s.wav")
    again, _ = NarrationCache(tmp_path / "tts2").narrate(
        RecordedAdapter(rec, cache_root=cache_root), text, "s"
    )
    for a, b in zip(narration.words, again.words, strict=True):
        assert abs(a.start - b.start) < 0.15
