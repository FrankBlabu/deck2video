"""Fetch the deck's Google Fonts once, store them locally, serve them during rendering.

A fallback font would shift line breaks, so fonts are part of the render's inputs: they are
downloaded once into the user cache and from then on served from disk. Rendering itself never
touches the network.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import httpx

from deck2video.deck import Deck, Face

CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
FONT_PREFIX = "/_fonts/"
_URL = re.compile(r"url\((['\"]?)(https?://[^)'\"]+)\1\)")

Fetch = Callable[[str], bytes]


class FontError(RuntimeError):
    pass


def default_cache_root() -> Path:
    """``$DECK2VIDEO_CACHE``, else ``$XDG_CACHE_HOME/deck2video``, else ``~/.cache/deck2video``."""
    if explicit := os.environ.get("DECK2VIDEO_CACHE"):
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "deck2video"


def http_fetch(url: str) -> bytes:
    resp = httpx.get(url, headers={"user-agent": CHROME_UA}, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def face_href(face: Face) -> str | None:
    if face.href:
        return face.href
    if face.src:
        return None
    family = quote_plus(face.family)
    return f"https://fonts.googleapis.com/css2?family={family}:ital,wght@0,400;0,600;0,700;1,400;1,700&display=swap"


class FontCache:
    """``<root>/fonts/<key>/fonts.css`` plus the font files it points to."""

    def __init__(self, root: Path | None = None, fetch: Fetch = http_fetch) -> None:
        self.root = (root or default_cache_root()) / "fonts"
        self.fetch = fetch

    def css_for(self, face: Face) -> str:
        if face.src:
            return (
                f"@font-face {{ font-family: '{face.family}'; "
                f"src: url('{face.src if face.src.startswith('/') else '/' + face.src}'); }}\n"
            )
        href = face_href(face)
        assert href is not None
        key = hashlib.sha256(href.encode()).hexdigest()[:16]
        folder = self.root / key
        css_file = folder / "fonts.css"
        if css_file.is_file():
            return css_file.read_text(encoding="utf-8")
        if urlparse(href).hostname != "fonts.googleapis.com":
            raise FontError(f"face {face.family!r}: only fonts.googleapis.com links are supported")
        try:
            css = self.fetch(href).decode("utf-8")
            files: dict[str, bytes] = {}
            for _, url in _URL.findall(css):
                name = (
                    hashlib.sha256(url.encode()).hexdigest()[:16] + Path(urlparse(url).path).suffix
                )
                if name not in files:
                    files[name] = self.fetch(url)
        except (httpx.HTTPError, OSError, UnicodeDecodeError) as exc:
            raise FontError(
                f"could not fetch font {face.family!r} ({exc}); a fallback font would shift line "
                "breaks, so the build stops. Fonts are fetched once and cached in "
                f"{self.root}"
            ) from exc

        def local(m: re.Match[str]) -> str:
            url = m.group(2)
            name = hashlib.sha256(url.encode()).hexdigest()[:16] + Path(urlparse(url).path).suffix
            return f"url({FONT_PREFIX}{key}/{name})"

        folder.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (folder / name).write_bytes(data)
        local_css = _URL.sub(local, css)
        tmp = css_file.with_suffix(".tmp")
        tmp.write_text(local_css, encoding="utf-8")
        tmp.replace(css_file)
        return local_css

    def deck_css(self, deck: Deck) -> str:
        """``@font-face`` rules for every face of the deck, pointing at local files."""
        return "".join(self.css_for(face) for face in deck.faces)

    def resolve(self, url_path: str) -> Path | None:
        """Map ``/_fonts/<key>/<file>`` to a cached file (``None`` if not one of ours)."""
        if not url_path.startswith(FONT_PREFIX):
            return None
        rel = url_path[len(FONT_PREFIX) :]
        parts = rel.split("/")
        if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
            return None
        path = self.root / parts[0] / parts[1]
        return path if path.is_file() else None
