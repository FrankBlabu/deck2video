from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

DeckFactory = Callable[..., Path]


def slide_html(slide_id: str, body: str, note: str, extra: str = "") -> str:
    return f'<section id="{slide_id}" class="light"{extra}>\n{body}\n<aside>{note}</aside>\n</section>\n'


@pytest.fixture
def make_deck(tmp_path: Path) -> DeckFactory:
    """Write a minimal deck: ``slides`` maps id -> full slide HTML."""

    def _make(
        slides: dict[str, str],
        *,
        order: list[str] | None = None,
        css: str = "section { font-family: sans-serif; }\n",
        faces: object = None,
        assets: dict[str, str] | None = None,
        cues: dict[str, str] | None = None,
        files: dict[str, bytes] | None = None,
    ) -> Path:
        root = tmp_path / "deck"
        (root / "project" / "slides").mkdir(parents=True)
        index: dict[str, object] = {"title": "Test deck", "order": order or list(slides)}
        if faces is not None:
            index["faces"] = faces
        (root / "project" / "deck.json").write_text(json.dumps(index), encoding="utf-8")
        (root / "project" / "deck.css").write_text(css, encoding="utf-8")
        for slide_id, html in slides.items():
            (root / "project" / "slides" / f"{slide_id}.html").write_text(html, encoding="utf-8")
        for name, data in (files or {}).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        if assets is not None:
            (root / "assets.json").write_text(json.dumps(assets), encoding="utf-8")
        for slide_id, text in (cues or {}).items():
            (root / "cues").mkdir(exist_ok=True)
            (root / "cues" / f"{slide_id}.yaml").write_text(text, encoding="utf-8")
        return root

    return _make
