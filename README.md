# deck2video

Narrated talk videos from a slide deck. Every input is text: the slides, their speaker notes
and a few lines of cues per slide. The tool narrates the notes, renders each slide in its real
CSS in headless Chromium, plays effects (zoom, pan, spotlight, outline, pulse, reveal) on the
words being spoken, and assembles an MP4 with subtitles.

It is a deterministic pipeline: an agent may *write* the cues, but `deck2video` validates them,
renders them and is the judge. Two builds from the same inputs produce the same frames.

```text
deck/ ──check──▶ notes ──ElevenLabs──▶ audio + word timestamps
                  cues ──────────────▶ effect timing (anchor word → frame)
               slides ──Chromium, virtual clock──▶ frames ──ffmpeg──▶ talk.mp4 + .srt + .vtt
                                                                   └─▶ build/<id>/timeline.json
```

## Install

Requirements: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg, and an
[ElevenLabs](https://elevenlabs.io) API key for narration.

```bash
git clone https://github.com/FrankBlabu/deck2video && cd deck2video
uv sync
uv run playwright install --with-deps chromium   # headless Chromium for rendering
sudo apt-get install -y ffmpeg                    # or: brew install ffmpeg
export ELEVENLABS_API_KEY=…                       # never put the key in a file in the deck
```

## Quick start

```bash
uv run deck2video check path/to/deck                       # validate, no rendering
uv run deck2video build path/to/deck --slides scale -o scale.mp4   # prototype one slide
uv run deck2video build path/to/deck -o talk.mp4 --best    # full talk, final quality
uv run deck2video build tests/fixtures/deck --voice-adapter fake -o demo.mp4  # offline demo
```

`build` always runs `check` first and stops on any problem. It writes `talk.mp4`, `talk.srt`,
`talk.vtt`, and `build/<slide>/timeline.json` for every slide.

## Input format

A deck folder as exported from the Slides artifact:

```text
deck/
├── project/
│   ├── deck.json            # "order": slide ids; "faces": Google Fonts families
│   ├── deck.css             # shared styles
│   └── slides/<id>.html     # one <section id="<id>"> per slide, 1920×1080
├── assets.json              # {"<asset id>": "assets/picture.png"} for /_blob/<id> references
└── cues/<id>.yaml           # optional, one per slide
```

- **Narration** is the slide's last child, `<aside>`. Its text is read exactly as written.
- **Hidden slides** (`<section hidden>`) are skipped.
- **Images** are referenced as `/_blob/<id>` and resolved through `assets.json` (paths
  relative to it; `project/assets.json` also works).
- **Fonts**: each `faces` entry (`{"family": …, "href": "https://fonts.googleapis.com/css2?…"}`,
  or a plain family name) is downloaded once into `~/.cache/deck2video/fonts` and served
  locally. Every other network request during rendering is blocked and fails the build — a
  fallback font would shift line breaks.
- **Slides elements**: `<x-shape>` and `<x-connector>` are drawn at render time.
- **The deck is never modified.** If a cue cannot target something, `check` says so; fix the
  cue, not the slide.

## Cues

A cue file is a list. Every cue has:

| key      | meaning                                                                 | default |
|----------|-------------------------------------------------------------------------|---------|
| `at`     | anchor phrase from the note; must occur **exactly once**                | –       |
| `effect` | one of `zoom`, `pan`, `spotlight`, `outline`, `pulse`, `reveal`         | –       |
| `target` | CSS selector; must match **exactly one** element of the slide           | –       |
| `hold`   | seconds the effect stays fully on (`2.5s`, `500ms` or a number)         | `2s`    |
| `ease`   | seconds to ease in, and again to ease out                               | `0.4s`  |

The cue starts on the first word of its anchor, at that word's timestamp in the narration,
within one frame. Anchors are matched word by word, ignoring case, surrounding punctuation and
typographic quotes. Selectors are evaluated like the browser does (`tbody` is implied in
tables; `svg circle:nth-of-type(4)` works); the narration `<aside>` is never a target.

### `zoom` — the camera moves in on the target

```yaml
- at: "nearly two thirds at a thousand"
  effect: zoom
  target: "svg circle:nth-of-type(4)"
  scale: 1.8          # 1 < scale ≤ 6, default 1.6
  hold: 2.5s
```

The target's box grows by exactly `scale`; the slide always fills the frame.

### `pan` — the camera glides across the target

```yaml
- at: "from left to right"
  effect: pan
  target: ".timeline"
  scale: 1.4                 # default 1.4
  direction: horizontal      # or vertical
  hold: 3s                   # duration of the glide, default 3s
```

### `spotlight` — everything else is dimmed

```yaml
- at: "The dashed blue line"
  effect: spotlight
  target: "svg polyline:nth-of-type(1)"
  dim: 0.65        # opacity of the darkening, default 0.65
  padding: 16      # px around the target, default 16
```

### `outline` — a frame is drawn around the target

```yaml
- at: "the formula"
  effect: outline
  target: "p.code"
  color: "#E07B28"   # default
  width: 6           # px, default 6
  padding: 12        # px, default 12
```

### `pulse` — a ring pulses around the target

```yaml
- at: "the orange ring"
  effect: pulse
  target: "svg rect:nth-of-type(1)"
  count: 2           # 1–6 pulses, never faster than two per second
  color: "#E07B28"
```

### `reveal` — children appear one after another

```yaml
- at: "There are three steps"
  effect: reveal
  target: "ul.list"    # its child elements are hidden until the cue
  step: 0.8s           # time between two children, default 0.6s
```

`reveal` controls the opacity of the target's children from the start of the slide.

Highlights (`spotlight`, `outline`, `pulse`) stack. The camera effects (`zoom`, `pan`) share
one camera: when two overlap, the later one takes over.

## Narration adapters

| adapter      | use                                                         |
|--------------|-------------------------------------------------------------|
| `elevenlabs` | default. Text-to-speech **with timestamps** (model `eleven_v3`); each note is read in one request, and the character alignment is turned into word timestamps. |
| `local`      | [Piper](https://github.com/OHF-Voice/piper1-gpl) neural TTS, offline after a one-time voice download; word timestamps by forced alignment. `pip install 'deck2video[local]'` |
| `recorded`   | your own recording per slide; word timestamps by forced alignment. `pip install 'deck2video[recorded]'` |
| `fake`       | offline tones with computed timestamps, for tests, CI and layout work. |

Choose one with `--voice-adapter`.

Narration is cached under `~/.cache/deck2video/tts` by a hash of the text, voice, model and
settings, so a rebuild never pays for the same sentence twice. The API key is read only from
`ELEVENLABS_API_KEY`, sent only in the request header, and never logged or cached.

### Adding or changing a voice

1. Pick a voice in the ElevenLabs voice library and copy its **voice id**.
2. Use it for one build with `--voice <id>`, or for all builds with
   `export ELEVENLABS_VOICE_ID=<id>`. The model is `eleven_v3` unless
   `--model` / `ELEVENLABS_MODEL_ID` say otherwise (`eleven_multilingual_v2` is the older,
   less expressive alternative).
3. Prototype on one or two slides (`--slides a,b`) before the whole deck: a changed voice
   re-narrates every slide, which costs ElevenLabs characters.

Without `--voice` the built-in default is the premade voice "George"
(`JBFqnCBsd6RMkjVDRZzb`).

### The `local` voice (Piper)

```bash
uv sync --extra local
uv run deck2video build deck --voice-adapter local -o talk.mp4                  # en_US-lessac-medium
uv run deck2video build deck --voice-adapter local --voice en_GB-alan-medium -o talk.mp4
```

`--voice` takes a [Piper voice name](https://huggingface.co/rhasspy/piper-voices). The voice
(about 60 MB) and the alignment model are downloaded once from Hugging Face into
`~/.cache/deck2video/`; after that everything runs offline on the CPU.

Licensing: `piper-tts` is GPL-3.0-or-later and each Piper voice has its own licence (see the
voice's model card). It is an optional extra; deck2video itself stays Apache-2.0 and does not
bundle Piper.

### The `recorded` voice (your own recordings)

Record each slide's note as written and save it as `recordings/<slide id>.wav` in the deck
(`.mp3`, `.m4a`, `.flac` and `.ogg` work too; `--recordings DIR` points elsewhere):

```text
deck/recordings/scale.wav
deck/recordings/horizon-forge.m4a
```

```bash
uv sync --extra recorded
uv run deck2video build deck --voice-adapter recorded -o talk.mp4
```

A missing or doubled recording stops the build before anything is rendered. Re-recording a
slide re-aligns only that slide.

### Forced alignment

`local` and `recorded` get word timestamps the same way: Whisper (faster-whisper, CPU)
transcribes the audio with word timings, and the transcript is matched to the note word by
word. Words heard as written take Whisper's times; a differently written run ("1,000" for "a
thousand") shares the time of what was heard; words Whisper missed are spread over the gap
between their neighbours; words it invented are ignored. The model is `base.en` by default;
`--align-model small.en` (or `DECK2VIDEO_ALIGN_MODEL`) is more accurate and slower.

### Writing an adapter

A new adapter implements `deck2video.tts.TTSAdapter`: `identity()`, everything that changes the
audio (hashed into the cache key, never a secret), and `synthesize(text, slide_id)`, which
returns audio plus one `Word(text, start, end)` per whitespace-separated word of the text.
Register it in `deck2video.tts.get_adapter`.

## Output, timing and determinism

- **Video**: 1920×1080, 30 fps, H.264 (yuv420p) + AAC. `-preset veryfast` by default;
  `--best` for the final render.
- **Slide length** = narration + pad: `--lead` (0.3 s) of silence before the note and
  `--tail` (0.9 s) after it, rounded to the frame grid.
- **Fades**: a slide with `data-transition` fades to black at its end; the next one fades in.
- **Subtitles**: `.srt` and `.vtt` from the same word timestamps (≤ 2 lines × 42 characters,
  ≤ 6 s, split at sentence ends).
- **Virtual clock**: nothing runs in real time. Every CSS animation, transition, Web Animation
  and SVG animation is paused and set to the frame's time; `performance.now`, `Date`,
  `requestAnimationFrame` and timers follow the same clock. The slide's own arrival animations
  (`.rise`) play on that clock.
- **Incremental builds**: each slide's segment is cached under `build/cache/segments/` by a
  hash of its HTML, CSS, assets, fonts, note, cues, voice, timing, quality and the tool
  version. Editing one note rebuilds one slide.

## The text twin: `timeline.json`

Every build writes `build/<id>/timeline.json` (and `frames.sha256`, one hash per frame).
For each frame it records the camera and every active effect with its phase, progress and the
target's bounding box on the 1920×1080 frame. Tests and agents read this instead of pixels:

```json
{"i": 1396, "t": 46.5333, "camera": {"scale": 1.8, "tx": -1536.0, "ty": -355.12},
 "effects": [{"cue": 3, "effect": "zoom", "target": "svg circle:nth-of-type(4)",
              "phase": "hold", "progress": 1.0, "box": [1189.54, 522.21, 32.4, 32.4]}]}
```

## Writing cues with an agent

1. `deck2video check deck` — learn the slide ids.
2. Read a slide's `<aside>` and markup; write `cues/<id>.yaml` with anchors copied from the
   note and selectors for single elements.
3. `deck2video check deck --slides <id>` until it prints `ok`; every error names the file, the
   cue number and what to change.
4. `deck2video build deck --slides <id> --voice-adapter fake` and read
   `build/<id>/timeline.json` to confirm timing and boxes, then build with the real voice.

## Rules and where they are enforced

| rule | enforced by |
|------|-------------|
| deck layout, one `<section>` per slide, `<aside>` last and non-empty, assets resolve | `check` (`tests/test_deck.py`) |
| anchor occurs exactly once; selector matches exactly one element; closed effect set; valid values | `check` (`tests/test_cues.py`) |
| cue starts within one frame of its anchor word | `tests/test_effects.py`, `tests/test_integration.py` |
| zoom target's box scales by `scale` | `tests/test_effects.py`, `tests/test_integration.py` |
| slide length = audio + pad; H.264 + AAC, 1920×1080, 30 fps | `tests/test_assemble.py`, `tests/test_integration.py` |
| no network while rendering; fonts are local | build error (`tests/test_fonts_render.py`) |
| the deck is never modified | `tests/test_deck.py`, `tests/test_fonts_render.py`, `tests/test_integration.py` |
| animations follow the virtual clock | `tests/test_clock.py` |
| two builds produce identical timelines and frames | `tests/test_integration.py` |
| editing one note rebuilds one slide | `tests/test_build.py` |

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run pytest -m live          # also calls the real ElevenLabs API (needs the key)
DECK2VIDEO_TEST_LOCAL_MODELS=1 uv run pytest -m live   # real Piper + Whisper (downloads models)
```

CI runs lint, type check and all tests (Chromium and ffmpeg included) on every push to
`main` and every pull request.

## License

Apache-2.0
