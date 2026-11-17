"""Cues: anchor phrase + effect + CSS selector, validated against the slide.

A cue file ``cues/<id>.yaml`` is a list of cues. Effects are a closed set::

    - at: "nearly two thirds at a thousand"   # phrase, exactly once in the note
      effect: zoom                            # zoom | pan | spotlight | outline | pulse | reveal
      target: "svg circle:nth-of-type(4)"     # selector, exactly one element on the slide
      scale: 1.8
      hold: 2.5s
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from selectolax.lexbor import LexborHTMLParser

from deck2video.deck import Deck, Slide, element_children
from deck2video.problems import Problem
from deck2video.text import find_phrase, phrase_span

EFFECTS = ("zoom", "pan", "spotlight", "outline", "pulse", "reveal")
CAMERA_EFFECTS = ("zoom", "pan")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)?\s*$")


def parse_duration(value: Any) -> Any:
    """Accept ``2.5s``, ``500ms`` or a plain number of seconds."""
    if isinstance(value, bool):
        raise ValueError("a duration such as '2.5s' or '500ms'")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        m = _DURATION.match(value)
        if not m:
            raise ValueError(f"{value!r} is not a duration such as '2.5s' or '500ms'")
        number = float(m.group(1))
        return number / 1000 if m.group(2) == "ms" else number
    return value


Duration = Annotated[float, BeforeValidator(parse_duration), Field(ge=0)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?$")]


class _CueBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: str = Field(min_length=1)
    """Anchor phrase: occurs exactly once in the slide's note; the cue starts on its first word."""
    target: str = Field(min_length=1)
    """CSS selector resolving to exactly one element of the slide."""
    hold: Duration = 2.0
    """Seconds the effect stays fully on, between ease-in and ease-out."""
    ease: Duration = 0.4
    """Seconds for easing in and again for easing out."""


class Zoom(_CueBase):
    """Camera zooms in on the target (its box scales by ``scale``), then back out."""

    effect: Literal["zoom"]
    scale: float = Field(1.6, gt=1, le=6)


class Pan(_CueBase):
    """Camera at ``scale`` glides across the target from one edge to the other."""

    effect: Literal["pan"]
    scale: float = Field(1.4, gt=1, le=6)
    direction: Literal["horizontal", "vertical"] = "horizontal"
    hold: Duration = 3.0


class Spotlight(_CueBase):
    """Everything except the target is dimmed."""

    effect: Literal["spotlight"]
    dim: float = Field(0.65, gt=0, le=1)
    padding: float = Field(16, ge=0, le=200)


class Outline(_CueBase):
    """A rounded outline is drawn around the target."""

    effect: Literal["outline"]
    color: Color = "#E07B28"
    width: float = Field(6, gt=0, le=32)
    padding: float = Field(12, ge=0, le=200)


class Pulse(_CueBase):
    """A ring around the target pulses ``count`` times (at most twice a second)."""

    effect: Literal["pulse"]
    color: Color = "#E07B28"
    count: int = Field(2, ge=1, le=6)
    padding: float = Field(12, ge=0, le=200)


class Reveal(_CueBase):
    """The target's element children are hidden until the cue, then appear in order."""

    effect: Literal["reveal"]
    step: Duration = 0.6
    """Seconds between two children appearing."""


Cue = Annotated[Zoom | Pan | Spotlight | Outline | Pulse | Reveal, Field(discriminator="effect")]


class _CueList(BaseModel):
    cues: list[Cue]


class ResolvedCue(BaseModel):
    """A validated cue with its anchor mapped to the note's word indices."""

    model_config = ConfigDict(frozen=True)

    slide_id: str
    index: int
    """Position in the cue file (0-based)."""
    cue: Cue
    word_start: int
    word_end: int

    @property
    def label(self) -> str:
        return f"cues/{self.slide_id}.yaml #{self.index + 1}"


def _cue_where(slide_id: str, i: int, raw: Any) -> str:
    at = raw.get("at") if isinstance(raw, dict) else None
    suffix = f" (at {at!r})" if isinstance(at, str) else ""
    return f"cues/{slide_id}.yaml #{i + 1}{suffix}"


def _format_validation(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"][3:]) or "cue"
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] == "extra_forbidden":
            msg = "unknown key"
        out.append(f"{loc}: {msg}")
    return out


def count_matches(tree: LexborHTMLParser, selector: str) -> tuple[int, str | None]:
    """Number of slide elements (outside the narration) matching ``selector``."""
    try:
        nodes = tree.css(selector)
    except Exception:
        return 0, f"invalid CSS selector {selector!r}"
    inside = [n for n in nodes if _in_slide(n)]
    return len(inside), None


def _in_slide(node: Any) -> bool:
    """A targetable slide element: not the section itself, not the narration."""
    if node.tag in ("section", "aside"):
        return False
    return not any(p.tag == "aside" for p in _ancestors(node))


def _ancestors(node: Any) -> list[Any]:
    out = []
    p = node.parent
    while p is not None:
        out.append(p)
        p = p.parent
    return out


def load_cues(deck: Deck, slide: Slide) -> tuple[list[ResolvedCue], list[Problem]]:
    """Parse and validate ``cues/<slide>.yaml``; missing file means no cues."""
    path = deck.cues_dir / f"{slide.id}.yaml"
    if not path.is_file():
        return [], []
    return validate_cues(slide, path)


def validate_cues(slide: Slide, path: Path) -> tuple[list[ResolvedCue], list[Problem]]:
    where = f"cues/{slide.id}.yaml"
    problems: list[Problem] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [], [Problem(where=where, message=f"invalid YAML: {exc}")]
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [Problem(where=where, message="must be a list of cues")]

    tree = LexborHTMLParser(slide.html)
    words = slide.words
    resolved: list[ResolvedCue] = []
    for i, item in enumerate(raw):
        cw = _cue_where(slide.id, i, item)
        if not isinstance(item, dict):
            problems.append(Problem(where=cw, message="a cue must be a mapping"))
            continue
        effect = item.get("effect")
        if effect not in EFFECTS:
            problems.append(
                Problem(
                    where=cw,
                    message=f"effect {effect!r} is not one of {', '.join(EFFECTS)}",
                )
            )
            continue
        try:
            cue = _CueList.model_validate({"cues": [item]}).cues[0]
        except ValidationError as exc:
            problems.extend(Problem(where=cw, message=m) for m in _format_validation(exc))
            continue

        ok = True
        hits = find_phrase(words, cue.at)
        if len(hits) != 1:
            ok = False
            problems.append(
                Problem(
                    where=cw,
                    message=(
                        "anchor phrase does not occur in the note"
                        if not hits
                        else f"anchor phrase occurs {len(hits)} times in the note; make it unique"
                    ),
                )
            )
        count, err = count_matches(tree, cue.target)
        if err:
            ok = False
            problems.append(Problem(where=cw, message=err))
        elif count != 1:
            ok = False
            problems.append(
                Problem(
                    where=cw,
                    message=f"target {cue.target!r} matches {count} elements on the slide; it must match exactly one",
                )
            )
        elif isinstance(cue, Reveal):
            node = _single(tree, cue.target)
            if node is not None and not element_children(node):
                ok = False
                problems.append(
                    Problem(where=cw, message=f"reveal target {cue.target!r} has no child elements")
                )
        if ok:
            start, end = phrase_span(words, hits[0], cue.at)
            resolved.append(
                ResolvedCue(slide_id=slide.id, index=i, cue=cue, word_start=start, word_end=end)
            )
    resolved.sort(key=lambda r: (r.word_start, r.index))
    return resolved, problems


def _single(tree: LexborHTMLParser, selector: str) -> Any:
    for node in tree.css(selector):
        if _in_slide(node):
            return node
    return None


def check_deck(
    deck: Deck, slide_ids: list[str] | None = None
) -> tuple[dict[str, list[ResolvedCue]], list[Problem]]:
    """Validate the cues of every (selected) visible slide; report stray cue files too."""
    problems: list[Problem] = []
    known = {s.id for s in deck.slides}
    if deck.cues_dir.is_dir():
        for path in sorted(deck.cues_dir.glob("*.yaml")):
            if path.stem not in known:
                problems.append(
                    Problem(
                        where=f"cues/{path.name}", message=f"no slide {path.stem!r} in the deck"
                    )
                )
    out: dict[str, list[ResolvedCue]] = {}
    for slide in select_slides(deck, slide_ids, problems):
        cues, cue_problems = load_cues(deck, slide)
        out[slide.id] = cues
        problems.extend(cue_problems)
    return out, problems


def select_slides(deck: Deck, slide_ids: list[str] | None, problems: list[Problem]) -> list[Slide]:
    if not slide_ids:
        return deck.visible_slides
    known = {s.id for s in deck.slides}
    for sid in slide_ids:
        if sid not in known:
            problems.append(Problem(where="--slides", message=f"no slide {sid!r} in the deck"))
    return [s for s in deck.visible_slides if s.id in slide_ids]
