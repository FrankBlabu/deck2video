from __future__ import annotations

import wave
from pathlib import Path

import pytest

from deck2video.assemble import (
    Pad,
    concat,
    encode_segment,
    pad_audio,
    segment_timing,
)
from deck2video.media import SAMPLE_RATE, probe, wav_duration
from deck2video.subtitles import captions, write_srt, write_vtt
from deck2video.tts import FakeAdapter, NarrationCache, Word
from tests.helpers import png

pytestmark = pytest.mark.slow


def test_segment_length_is_audio_plus_pad() -> None:
    pad = Pad(lead=0.3, tail=0.9)
    t = segment_timing(10.0, pad)
    assert t.frames == 336
    assert t.duration == pytest.approx(11.2)
    assert segment_timing(10.01, pad).frames == 336  # rounded to the frame grid


def test_encoded_segments_and_final_mp4(tmp_path: Path) -> None:
    pad = Pad()
    cache = NarrationCache(tmp_path / "tts")
    frame_a, frame_b = png(1920, 1080, (240, 230, 220)), png(1920, 1080, (20, 30, 60))
    segments, audio, offsets, all_caps = [], [], [], []
    offset = 0.0
    for i, (text, frame) in enumerate(
        [
            ("First slide speaks briefly. Then stops.", frame_a),
            ("Second slide, also short.", frame_b),
        ]
    ):
        narration, _ = cache.narrate(FakeAdapter(), text)
        timing = segment_timing(narration.duration, pad)
        seg = tmp_path / f"seg{i}.mp4"
        encode_segment(
            (frame for _ in range(timing.frames)),
            seg,
            timing.frames,
            fade_in=pad.fade_in if i else 0,
            fade_out=pad.fade_out,
        )
        info = probe(seg)
        video = info["streams"][0]
        assert video["codec_name"] == "h264"
        assert int(video["nb_frames"]) == timing.frames
        assert float(info["format"]["duration"]) == pytest.approx(
            narration.duration + pad.total, abs=1 / 30
        )
        wav = tmp_path / f"seg{i}.wav"
        pad_audio(narration.audio, wav, pad.lead, timing.duration)
        assert wav_duration(wav) == pytest.approx(timing.duration, abs=1 / SAMPLE_RATE)
        segments.append(seg)
        audio.append(wav)
        offsets.append(offset)
        all_caps += captions(narration.words, offset + pad.lead)
        offset += timing.duration

    out = tmp_path / "talk.mp4"
    concat(segments, audio, out, tmp_path / "work")
    info = probe(out)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (1920, 1080)
    assert kinds["video"]["r_frame_rate"] == "30/1"
    assert kinds["video"]["pix_fmt"] == "yuv420p"
    assert kinds["audio"]["codec_name"] == "aac"
    assert float(info["format"]["duration"]) == pytest.approx(offset, abs=0.05)
    assert int(kinds["video"]["nb_frames"]) == round(offset * 30)

    srt, vtt = tmp_path / "talk.srt", tmp_path / "talk.vtt"
    write_srt(all_caps, srt)
    write_vtt(all_caps, vtt)
    assert len(all_caps) == 3
    assert srt.read_text().count(" --> ") == 3
    assert vtt.read_text().startswith("WEBVTT\n\n")
    assert all_caps[2].start == pytest.approx(offsets[1] + pad.lead + 0.2, abs=1e-3)


def test_padded_audio_is_exact(tmp_path: Path) -> None:
    narration, _ = NarrationCache(tmp_path).narrate(FakeAdapter(), "One two.")
    out = tmp_path / "padded.wav"
    pad_audio(narration.audio, out, 0.5, 3.0)
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() == 3 * SAMPLE_RATE
        head = w.readframes(int(0.5 * SAMPLE_RATE))
    assert set(head) == {0}


def test_captions_group_and_time_words() -> None:
    words = [
        Word(text=t, start=i * 0.5, end=i * 0.5 + 0.4)
        for i, t in enumerate(
            [
                "This",
                "is",
                "the",
                "first",
                "sentence.",
                "And",
                "here",
                "comes",
                "a",
                "much",
                "longer",
                "second",
                "sentence",
                "that",
                "certainly",
                "needs",
                "to",
                "be",
                "wrapped",
                "onto",
                "two",
                "lines",
                "or",
                "split.",
            ]
        )
    ]
    caps = captions(words, offset=10)
    assert caps[0].text == "This is the first sentence."
    assert caps[0].start == 10
    assert caps[0].end == pytest.approx(10 + 4 * 0.5 + 0.4)
    for c in caps:
        lines = c.text.split("\n")
        assert len(lines) <= 2
        assert all(len(line) <= 42 for line in lines)
        assert c.end - c.start <= 6.0
    assert " ".join(c.text.replace("\n", " ") for c in caps) == " ".join(w.text for w in words)
