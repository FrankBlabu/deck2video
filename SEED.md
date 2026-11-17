# Seed: deck-to-video – narrated talk videos from a slide deck

> New repository. The Builder starts from an empty repo; everything it needs is here.
> Builder hint: extended-thinking – the virtual clock (Constraints) is the hard part.

## Why

The talk "Project Horizon – Letting a product converge" is a slide deck in which every slide
carries its full spoken text as speaker notes. We want the talk as a video: synthetic narration,
plus effects on each slide (zoom, spotlight, highlight, step-by-step reveal) that are timed to
the words being spoken. A video editor would make this manual and impossible to keep in sync.
The principle is "tools before agents": a deterministic pipeline whose inputs are all text. An
agent may write the cues, but the tool validates them, renders them and is the judge.

## Input

A deck folder, as exported from the Slides artifact:

- `project/deck.json` – `order` (slide ids), `faces` (Google Fonts families)
- `project/deck.css` – shared styles
- `project/slides/<id>.html` – one `<section id="<id>">` per slide, on a 1920×1080 canvas. The
  narration is the slide's last child, `<aside>`.
- images referenced as `/_blob/<id>`, plus a mapping file `assets.json` (`id → file`)
- `cues/<id>.yaml` – optional, one per slide (see below)

## Outcome

- One CLI: `deck2video build <deck-dir> -o talk.mp4 [--slides a,b] [--voice …]`, plus
  `deck2video check <deck-dir>`, which validates without rendering.
- **Narration:** the `<aside>` text goes through a pluggable TTS adapter that returns audio and
  word-level timestamps. Adapters:
  - `local` – default, installable from PyPI, works offline
  - `cloud` – optional, e.g. ElevenLabs or Azure, with the key read from the environment
  - `recorded` – user-supplied WAV per slide, with forced alignment for the timestamps
- **Cues, as text:** each cue is an anchor phrase from the note, an effect, a CSS selector and a
  duration. The effects form a closed set: `zoom`, `pan`, `spotlight` (dim everything else),
  `outline`, `pulse` and `reveal` (siblings in order). Example:
  ```yaml
  - at: "nearly two thirds at a thousand"
    effect: zoom
    target: "svg circle:nth-of-type(4)"
    scale: 1.8
    hold: 2.5s
  - at: "The dashed blue line"
    effect: spotlight
    target: "svg polyline:nth-of-type(1)"
  ```
- **Rendering:** a headless Chromium (Playwright) renders the slide in the deck's real CSS.
  Effects are applied as overlay transforms and never edit the slide HTML. The slide's own
  arrival animations (`.rise`, `data-transition`) play on the same clock.
- **Assembly:** ffmpeg builds the video per slide, adds a fade between slides, concatenates the
  slides, and writes `.srt` and `.vtt` from the same word timestamps.
- **Incremental builds:** each slide's segment is cached under a hash of its inputs (HTML, CSS,
  note, cues, voice, tool version). Editing one note rebuilds one slide.
- **Text twin:** every build writes `build/<id>/timeline.json`, which records for each frame the
  active effects and the target's bounding box. Tests and agents read this, not the pixels.

## Tests

- **Unit:** deck parsing (order, notes, assets), and cue validation (every anchor phrase occurs
  in its note exactly once, every selector resolves to exactly one element on the slide, every
  effect is from the closed set). Each of these fails with a precise message.
- **Timing:** a cue starts within one frame of its anchor word's timestamp, and a slide's video
  length equals its audio length plus the configured pad.
- **Integration:** a two-slide fixture deck checked into `tests/fixtures/` builds to an MP4 with
  the expected duration, streams and subtitle count. `timeline.json` shows the zoom target's box
  scaled at the cue's frame.
- **Determinism:** two builds from the same inputs produce an identical `timeline.json` and
  identical frame hashes.

## Done

- [ ] `check` and `build` work on the fixture deck and on the real deck (attached by the
      Orchestrator). Prototype first: slides `scale` and `horizon-forge`, then all slides.
- [ ] README: input format, cue reference with one example per effect, adapters, how to add a
      voice.
- [ ] Typed Python (Pydantic models for deck, cues and timeline), Ruff, a type checker, CI on
      push.
- [ ] Every rule above is a test or a `check` error, not prose.

## Constraints

- **Virtual clock, not real time.** Pause every Web Animation and SVG animation and set its
  `currentTime` per frame. A screen recording or sleeps would be flaky and non-deterministic.
- **Fonts must be local.** Fetch the `faces` from `deck.json` once and serve them locally; a
  fallback font shifts line breaks. Block every other network request during rendering.
- **The video is 1920×1080 at 30 fps, H.264 and AAC.** Use `-preset veryfast` by default and a
  `--best` flag for the final render.
- **Never change the deck's files.** If a cue can't target something, report it; don't patch the
  slide.
- **Out of scope:** an editing UI, automatic cue writing inside the tool (an agent may draft
  `cues/*.yaml`; the tool only validates and renders), and music.
- **Open for the Builder:** which local TTS engine to use (clarity versus install size), and
  whether forced alignment is needed for the `local` adapter or the engine already provides
  timestamps.
