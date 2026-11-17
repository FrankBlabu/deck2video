"""Command line interface: ``deck2video check`` and ``deck2video build``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from deck2video import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deck2video",
        description="Turn a slide deck with speaker notes into a narrated video.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate a deck and its cues without rendering")
    check.add_argument("deck", type=Path, help="deck directory")

    build = sub.add_parser("build", help="render the deck to a narrated MP4")
    build.add_argument("deck", type=Path, help="deck directory")
    build.add_argument("-o", "--output", type=Path, default=Path("talk.mp4"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(f"deck2video {args.command}: not implemented yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
