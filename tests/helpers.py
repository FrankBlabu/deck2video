"""Shared test helpers (no fixtures)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SYSTEM_FONTS = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    *sorted(Path("/usr/share/fonts").rglob("*.ttf")),
]


def system_font() -> bytes | None:
    for path in SYSTEM_FONTS:
        if path.is_file():
            return path.read_bytes()
    return None


def png(width: int = 4, height: int = 4, rgb: tuple[int, int, int] = (200, 50, 50)) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class StubFetch:
    """Stands in for Google Fonts: one CSS file pointing at one font file."""

    def __init__(self, family: str, font: bytes) -> None:
        self.family = family
        self.font = font
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url.startswith("https://fonts.googleapis.com/"):
            return (
                f"@font-face {{ font-family: '{self.family}'; font-style: normal; "
                "font-weight: 400; src: url(https://fonts.gstatic.com/s/test/v1/face.ttf) "
                "format('truetype'); }\n"
            ).encode()
        if url == "https://fonts.gstatic.com/s/test/v1/face.ttf":
            return self.font
        raise OSError(f"unexpected fetch {url}")
