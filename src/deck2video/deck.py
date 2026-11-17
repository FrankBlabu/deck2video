"""Parse a deck folder (as exported from the Slides artifact) into typed models.

Layout::

    <deck>/project/deck.json        order, faces, title
    <deck>/project/deck.css         shared styles
    <deck>/project/slides/<id>.html one <section id="<id>"> per slide; last child <aside> = narration
    <deck>/assets.json              /_blob/<id> -> file (optional; also project/assets.json)
    <deck>/cues/<id>.yaml           optional cues per slide

The deck is only ever read, never written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser, LexborNode

from deck2video.problems import DeckError, Problem
from deck2video.text import normalize_text, words

BLOB_RE = re.compile(r"/_blob/([A-Za-z0-9_.-]+)")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Face(BaseModel):
    """A typeface from ``deck.json`` ``faces``."""

    key: str
    family: str
    href: str | None = None
    src: str | None = None


class Slide(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    path: Path
    html: str
    """The slide file's content, unmodified."""
    note: str
    """Narration: the ``<aside>`` text, whitespace-normalised."""
    hidden: bool = False
    transition: str | None = None
    blobs: tuple[str, ...] = ()

    @property
    def words(self) -> list[str]:
        return words(self.note)


class Deck(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: Path
    title: str
    slides: tuple[Slide, ...]
    """All slides in deck order (hidden ones included, flagged)."""
    faces: tuple[Face, ...]
    css: str
    assets: dict[str, Path]
    """Asset id -> absolute file path."""

    @property
    def cues_dir(self) -> Path:
        return self.root / "cues"

    @property
    def visible_slides(self) -> list[Slide]:
        return [s for s in self.slides if not s.hidden]

    def slide(self, slide_id: str) -> Slide:
        for s in self.slides:
            if s.id == slide_id:
                return s
        raise KeyError(slide_id)


def element_children(node: LexborNode) -> list[LexborNode]:
    return [c for c in node.iter(include_text=False) if c.tag not in ("-comment", "-text")]


def _faces(raw: Any, problems: list[Problem]) -> list[Face]:
    out: list[Face] = []
    if raw is None:
        return out
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, dict) and isinstance(val.get("family"), str):
                out.append(
                    Face(key=key, family=val["family"], href=val.get("href"), src=val.get("src"))
                )
            elif isinstance(val, str):
                out.append(Face(key=key, family=val))
            else:
                problems.append(Problem(where="deck.json", message=f"face {key!r} has no 'family'"))
    elif isinstance(raw, list):
        for val in raw:
            if isinstance(val, str):
                out.append(Face(key=val, family=val))
            else:
                problems.append(
                    Problem(where="deck.json", message=f"face entry {val!r} is not a string")
                )
    else:
        problems.append(Problem(where="deck.json", message="'faces' must be an object or a list"))
    return out


def _assets(root: Path, problems: list[Problem]) -> dict[str, Path]:
    for candidate in (root / "assets.json", root / "project" / "assets.json"):
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                problems.append(Problem(where=candidate.name, message=f"invalid JSON: {exc}"))
                return {}
            if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
                problems.append(
                    Problem(where=candidate.name, message="must map asset id to a file name")
                )
                return {}
            out: dict[str, Path] = {}
            for asset_id, rel in raw.items():
                path = (candidate.parent / rel).resolve()
                if not path.is_file():
                    problems.append(
                        Problem(
                            where=candidate.name,
                            message=f"asset {asset_id!r}: file {rel!r} not found",
                        )
                    )
                out[asset_id] = path
            return out
    return {}


def _parse_slide(slide_id: str, path: Path, problems: list[Problem]) -> Slide | None:
    where = f"slides/{slide_id}.html"
    if not path.is_file():
        problems.append(Problem(where=where, message="slide file not found"))
        return None
    html = path.read_text(encoding="utf-8")
    tree = LexborHTMLParser(html)
    sections = tree.css("section")
    if len(sections) != 1:
        problems.append(
            Problem(where=where, message=f"expected exactly one <section>, found {len(sections)}")
        )
        return None
    section = sections[0]
    attrs = section.attributes
    if attrs.get("id") != slide_id:
        problems.append(
            Problem(
                where=where,
                message=f'<section id="{attrs.get("id")}"> does not match slide id {slide_id!r}',
            )
        )
    children = element_children(section)
    asides = section.css("aside")
    note = ""
    if not asides:
        problems.append(Problem(where=where, message="no <aside> narration"))
    elif len(asides) > 1:
        problems.append(Problem(where=where, message=f"expected one <aside>, found {len(asides)}"))
    elif not children or children[-1].tag != "aside":
        problems.append(Problem(where=where, message="<aside> must be the last child of <section>"))
    else:
        note = normalize_text(asides[0].text())
        if not note:
            problems.append(Problem(where=where, message="<aside> narration is empty"))
    return Slide(
        id=slide_id,
        path=path,
        html=html,
        note=note,
        hidden="hidden" in attrs,
        transition=attrs.get("data-transition"),
        blobs=tuple(dict.fromkeys(BLOB_RE.findall(html))),
    )


def parse_deck(root: Path) -> tuple[Deck | None, list[Problem]]:
    """Parse ``root``; return the deck (``None`` if unusable) and every problem found."""
    problems: list[Problem] = []
    root = root.resolve()
    index = root / "project" / "deck.json"
    if not index.is_file():
        return None, [Problem(where=str(root), message="project/deck.json not found")]
    try:
        raw = json.loads(index.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [Problem(where="deck.json", message=f"invalid JSON: {exc}")]
    order = raw.get("order") if isinstance(raw, dict) else None
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order) or not order:
        return None, [
            Problem(where="deck.json", message="'order' must be a non-empty list of slide ids")
        ]
    for dup in sorted({i for i in order if order.count(i) > 1}):
        problems.append(
            Problem(where="deck.json", message=f"slide id {dup!r} appears twice in 'order'")
        )
    css_path = root / "project" / "deck.css"
    css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
    if not css_path.is_file():
        problems.append(Problem(where="project/deck.css", message="file not found"))
    faces = _faces(raw.get("faces"), problems)
    assets = _assets(root, problems)

    slides: list[Slide] = []
    for slide_id in dict.fromkeys(order):
        if not ID_RE.match(slide_id):
            problems.append(Problem(where="deck.json", message=f"invalid slide id {slide_id!r}"))
            continue
        slide = _parse_slide(slide_id, root / "project" / "slides" / f"{slide_id}.html", problems)
        if slide is not None:
            slides.append(slide)

    refs: list[tuple[str, str]] = [(f"slides/{s.id}.html", b) for s in slides for b in s.blobs]
    refs += [("project/deck.css", b) for b in BLOB_RE.findall(css)]
    refs += [(f"deck.json face {f.key!r}", b) for f in faces for b in BLOB_RE.findall(f.src or "")]
    for where, blob in refs:
        if blob not in assets:
            problems.append(
                Problem(where=where, message=f"/_blob/{blob} is not listed in assets.json")
            )

    title = raw.get("title") if isinstance(raw.get("title"), str) else root.name
    deck = Deck(
        root=root,
        title=title or root.name,
        slides=tuple(slides),
        faces=tuple(faces),
        css=css,
        assets=assets,
    )
    return deck, problems


def load_deck(root: Path) -> Deck:
    """Parse ``root`` or raise :class:`DeckError` listing every problem."""
    deck, problems = parse_deck(root)
    if problems or deck is None:
        raise DeckError(problems)
    return deck
