from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from deck2video.text import words
from deck2video.tts import (
    ElevenLabsAdapter,
    FakeAdapter,
    NarrationCache,
    Synthesis,
    TTSError,
    alignment_to_words,
    get_adapter,
)

TEXT = "Take 0.1 percent – it’s “demo-versus-product” 1,000 times."


def char_alignment(text: str) -> dict[str, list[Any]]:
    chars = list(text)
    return {
        "characters": chars,
        "character_start_times_seconds": [i * 0.05 for i in range(len(chars))],
        "character_end_times_seconds": [i * 0.05 + 0.04 for i in range(len(chars))],
    }


def test_alignment_to_words_matches_note_tokens() -> None:
    al = char_alignment(TEXT)
    got = alignment_to_words(
        TEXT,
        al["characters"],
        al["character_start_times_seconds"],
        al["character_end_times_seconds"],
    )
    assert [w.text for w in got] == words(TEXT)
    assert [w.text for w in got][1] == "0.1"
    assert got[3].text == "–"
    assert got[5].text == "“demo-versus-product”"
    assert got[0].start == 0.0
    assert got[0].end == pytest.approx(3 * 0.05 + 0.04)
    assert got[1].start == pytest.approx(5 * 0.05)


def test_alignment_must_reproduce_text() -> None:
    with pytest.raises(TTSError, match="do not reproduce"):
        alignment_to_words("ab", ["a", "c"], [0, 1], [1, 2])
    with pytest.raises(TTSError, match="differ in length"):
        alignment_to_words("ab", ["a", "b"], [0], [1, 2])


class Counting(FakeAdapter):
    calls = 0

    def synthesize(self, text: str) -> Synthesis:
        Counting.calls += 1
        return super().synthesize(text)


def test_cache_hit_and_miss(tmp_path: Path) -> None:
    cache = NarrationCache(tmp_path)
    adapter = Counting()
    first, cached = cache.narrate(adapter, "Hello   there, world.")
    assert not cached
    assert first.audio.is_file()
    assert [w.text for w in first.words] == ["Hello", "there,", "world."]
    assert first.duration > first.words[-1].end
    again, cached = cache.narrate(adapter, "Hello there, world.")
    assert cached
    assert again == first
    assert Counting.calls == 1
    other, cached = cache.narrate(Counting(per_char=0.06), "Hello there, world.")
    assert not cached
    assert Counting.calls == 2
    assert other.audio != first.audio


def test_fake_adapter_is_deterministic(tmp_path: Path) -> None:
    a = NarrationCache(tmp_path / "a").narrate(FakeAdapter(), "One two three.")[0]
    b = NarrationCache(tmp_path / "b").narrate(FakeAdapter(), "One two three.")[0]
    assert a.audio.read_bytes() == b.audio.read_bytes()
    assert a.words == b.words


def test_word_count_mismatch_is_an_error(tmp_path: Path) -> None:
    class Broken(FakeAdapter):
        def synthesize(self, text: str) -> Synthesis:
            s = super().synthesize(text)
            return Synthesis(audio=s.audio, suffix=s.suffix, words=s.words[:-1])

    with pytest.raises(TTSError, match="cover 1 words, the note has 2"):
        NarrationCache(tmp_path).narrate(Broken(), "Two words")


def mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_elevenlabs_request_and_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"ID3fake").decode(),
                "alignment": char_alignment(body["text"]),
            },
        )

    adapter = ElevenLabsAdapter(voice="v123", api_key="secret-key", client=mock_client(handler))
    result = adapter.synthesize("Hello world.")
    assert result.audio == b"ID3fake"
    assert result.suffix == ".mp3"
    assert [w.text for w in result.words] == ["Hello", "world."]
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/v123/with-timestamps"
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "secret-key"
    assert json.loads(req.content)["model_id"] == "eleven_multilingual_v2"
    assert "secret-key" not in json.dumps(adapter.identity())


def test_elevenlabs_retries_on_429_and_5xx() -> None:
    statuses = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status != 200:
            return httpx.Response(status, text="busy")
        return httpx.Response(
            200,
            json={"audio_base64": "", "alignment": char_alignment("Hi")},
        )

    adapter = ElevenLabsAdapter(api_key="k", client=mock_client(handler), sleep=sleeps.append)
    assert [w.text for w in adapter.synthesize("Hi").words] == ["Hi"]
    assert sleeps == [1.0, 2.0]


def test_elevenlabs_gives_up_after_retries() -> None:
    adapter = ElevenLabsAdapter(
        api_key="k",
        client=mock_client(lambda r: httpx.Response(500)),
        sleep=lambda s: None,
        retries=2,
    )
    with pytest.raises(TTSError, match="error 500 after 3 attempts"):
        adapter.synthesize("Hi")


def test_elevenlabs_rejected_key() -> None:
    adapter = ElevenLabsAdapter(api_key="k", client=mock_client(lambda r: httpx.Response(401)))
    with pytest.raises(TTSError, match="rejected the API key"):
        adapter.synthesize("Hi")


def test_elevenlabs_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY is not set"):
        ElevenLabsAdapter().synthesize("Hi")


def test_voice_and_model_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "envvoice")
    adapter = get_adapter("elevenlabs")
    assert isinstance(adapter, ElevenLabsAdapter)
    assert adapter.voice == "envvoice"
    assert get_adapter("elevenlabs", voice="cli").identity()["voice"] == "cli"
    with pytest.raises(TTSError, match="unknown voice adapter"):
        get_adapter("nope")


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ELEVENLABS_API_KEY"), reason="needs ELEVENLABS_API_KEY")
def test_elevenlabs_live(tmp_path: Path) -> None:
    narration, _ = NarrationCache(tmp_path).narrate(ElevenLabsAdapter(), "Testing one, two.")
    assert [w.text for w in narration.words] == ["Testing", "one,", "two."]
    assert narration.duration > 0.5
