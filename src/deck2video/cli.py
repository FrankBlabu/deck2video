"""Command line interface: ``deck2video check`` and ``deck2video build``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from deck2video import __version__
from deck2video.assemble import Pad
from deck2video.build import BlockedRequests, BuildOptions, build
from deck2video.cues import check_deck
from deck2video.deck import parse_deck
from deck2video.fonts import FontError
from deck2video.media import MediaError
from deck2video.problems import DeckError, Problem
from deck2video.tts import ADAPTERS, TTSError


def _slides_arg(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deck2video",
        description="Turn a slide deck with speaker notes into a narrated video.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate a deck and its cues without rendering")
    check.add_argument("deck", type=Path, help="deck directory")
    check.add_argument("--slides", type=_slides_arg, help="comma-separated slide ids")

    build = sub.add_parser("build", help="render the deck to a narrated MP4")
    build.add_argument("deck", type=Path, help="deck directory")
    build.add_argument("-o", "--output", type=Path, default=Path("talk.mp4"))
    build.add_argument("--slides", type=_slides_arg, help="comma-separated slide ids")
    build.add_argument(
        "--voice", help="ElevenLabs voice id (default: $ELEVENLABS_VOICE_ID or a built-in voice)"
    )
    build.add_argument("--model", help="ElevenLabs model id (default: eleven_multilingual_v2)")
    build.add_argument(
        "--recordings",
        type=Path,
        help="folder with one recording per slide for --voice-adapter recorded (default: <deck>/recordings)",
    )
    build.add_argument(
        "--align-model",
        help="Whisper model for forced alignment (local/recorded voices; default: base.en)",
    )
    build.add_argument(
        "--voice-adapter",
        choices=ADAPTERS,
        default="elevenlabs",
        help="narration: elevenlabs (default), local (Piper, offline), recorded (your audio), fake (test tones)",
    )
    build.add_argument("--best", action="store_true", help="slow, high-quality final encode")
    build.add_argument("--build-dir", type=Path, default=Path("build"))
    build.add_argument("--lead", type=float, default=Pad().lead, help="silence before the note (s)")
    build.add_argument("--tail", type=float, default=Pad().tail, help="silence after the note (s)")
    return parser


def report(problems: list[Problem]) -> None:
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    print(f"{len(problems)} problem(s) found", file=sys.stderr)


def run_check(deck_dir: Path, slide_ids: list[str] | None) -> int:
    deck, problems = parse_deck(deck_dir)
    if deck is None:
        report(problems)
        return 1
    cues, cue_problems = check_deck(deck, slide_ids)
    problems += cue_problems
    if problems:
        report(problems)
        return 1
    n_cues = sum(len(c) for c in cues.values())
    print(f"ok: {len(cues)} slide(s), {n_cues} cue(s)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        return run_check(args.deck, args.slides)
    return run_build(args)


def run_build(args: argparse.Namespace) -> int:
    opts = BuildOptions(
        deck=args.deck,
        output=args.output,
        build_dir=args.build_dir,
        slides=args.slides,
        adapter=args.voice_adapter,
        voice=args.voice,
        model=args.model,
        recordings=args.recordings,
        align_model=args.align_model,
        best=args.best,
        pad=Pad(lead=args.lead, tail=args.tail),
    )
    try:
        build(opts)
    except DeckError as exc:
        report(exc.problems)
        return 1
    except (TTSError, FontError, MediaError, BlockedRequests) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
