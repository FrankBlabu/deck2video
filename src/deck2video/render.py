"""Headless Chromium that shows one slide in the deck's real CSS, offline.

Every request is answered from local files (page, ``deck.css``, ``/_blob/`` assets, cached
fonts, ``project/`` files); everything else is aborted and recorded in ``blocked``.
Slide files are never modified: the page is assembled in memory.
"""

from __future__ import annotations

import html
import mimetypes
import os
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlparse

from playwright.sync_api import Browser, Page, Playwright, Route, sync_playwright

from deck2video.deck import Deck, Slide
from deck2video.fonts import FontCache

WIDTH, HEIGHT = 1920, 1080
ORIGIN = "http://deck2video.invalid"

BASE_CSS = """
html, body { margin: 0; padding: 0; width: 1920px; height: 1080px; overflow: hidden; background: #000; }
#d2v-stage { position: relative; width: 1920px; height: 1080px; overflow: hidden; }
#d2v-camera { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; }
#d2v-camera > section { position: relative; width: 1920px; height: 1080px; box-sizing: border-box;
  overflow: hidden; margin: 0; }
#d2v-camera > section[hidden] { display: block; }
#d2v-camera aside { display: none !important; }
h1, h2, h3, p { margin: 0; }
h1 { font-size: 96px; font-weight: 600; }
h2 { font-size: 64px; font-weight: 600; }
h3 { font-size: 44px; font-weight: 600; }
p { font-size: 32px; font-weight: 400; }
#d2v-overlay { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px;
  pointer-events: none; overflow: hidden; }
"""

CHROMIUM_ARGS = [
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--force-color-profile=srgb",
    "--disable-gpu",
    "--hide-scrollbars",
    "--mute-audio",
    # determinism: decode images synchronously, finish every compositor stage before a frame
    "--disable-checker-imaging",
    "--run-all-compositor-stages-before-draw",
    "--disable-partial-raster",
    "--disable-threaded-animation",
    "--disable-image-animation-resync",
]


def page_html(slide: Slide, font_css: str, init_script: str = "") -> str:
    """The document a slide is rendered in; the slide's markup is inserted verbatim."""
    script = f"<script>{init_script}</script>" if init_script else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{script}<title>{html.escape(slide.id)}</title>"
        f"<style id='d2v-fonts'>{font_css}</style>"
        f"<style id='d2v-base'>{BASE_CSS}</style>"
        "<link rel='stylesheet' href='/deck.css'>"
        f"</head><body><div id='d2v-stage'><div id='d2v-camera'>{slide.html}</div>"
        "<div id='d2v-overlay'></div></div></body></html>"
    )


class Renderer:
    """One browser for a whole build; :meth:`open` loads a slide into a fresh page."""

    def __init__(self, deck: Deck, fonts: FontCache) -> None:
        self.deck = deck
        self.fonts = fonts
        self.font_css = fonts.deck_css(deck)
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> Renderer:
        self._pw = sync_playwright().start()
        exe = os.environ.get("DECK2VIDEO_CHROMIUM")
        self._browser = self._pw.chromium.launch(args=CHROMIUM_ARGS, executable_path=exe or None)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def _local_file(self, path: str) -> Path | None:
        if path == "/deck.css":
            return None
        if path.startswith("/_blob/"):
            return self.deck.assets.get(path[len("/_blob/") :])
        font = self.fonts.resolve(path)
        if font is not None:
            return font
        if path.startswith("/project/"):
            candidate = (self.deck.root / path.lstrip("/")).resolve()
            project = (self.deck.root / "project").resolve()
            if candidate.is_file() and project in candidate.parents:
                return candidate
        return None

    def open(self, slide: Slide, init_script: str | None = None) -> SlidePage:
        """Load ``slide`` with the virtual clock installed (or a custom ``init_script``)."""
        if init_script is None:
            from deck2video.clock import CLOCK_JS

            init_script = CLOCK_JS
        assert self._browser is not None, "use Renderer as a context manager"
        context = self._browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            reduced_motion="no-preference",
            color_scheme="light",
            locale="en-US",
            timezone_id="UTC",
            java_script_enabled=True,
        )
        page = context.new_page()
        blocked: list[str] = []
        document = page_html(slide, self.font_css, init_script)
        css = self.deck.css

        def handle(route: Route) -> None:
            url = route.request.url
            parsed = urlparse(url)
            if f"{parsed.scheme}://{parsed.netloc}" == ORIGIN:
                path = unquote(parsed.path)
                if path in ("/", "/index.html"):
                    route.fulfill(status=200, content_type="text/html", body=document)
                    return
                if path == "/deck.css":
                    route.fulfill(status=200, content_type="text/css", body=css)
                    return
                local = self._local_file(path)
                if local is not None:
                    ctype = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
                    route.fulfill(status=200, content_type=ctype, body=local.read_bytes())
                    return
            if parsed.scheme == "data":
                route.continue_()
                return
            blocked.append(url)
            route.abort("blockedbyclient")

        page.route("**/*", handle)
        page.goto(f"{ORIGIN}/", wait_until="load")
        page.evaluate(
            """async () => {
                await document.fonts.ready;
                await Promise.all([...document.images].map((img) => img.decode().catch(() => null)));
                return true;
            }"""
        )
        if "__d2v" in init_script:
            page.evaluate("() => window.__d2v.step(0)")
        return SlidePage(page=page, slide=slide, blocked=blocked)


class SlidePage:
    def __init__(self, page: Page, slide: Slide, blocked: list[str]) -> None:
        self.page = page
        self.slide = slide
        self.blocked = blocked

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="png", animations="allow", caret="hide")

    def evaluate(self, script: str, arg: Any = None) -> Any:
        return self.page.evaluate(script, arg)

    def close(self) -> None:
        self.page.context.close()
