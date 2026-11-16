"""A frame of RGB pixels with clipping drawing primitives and stdlib-only encoders."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arthur.render.font import Font

RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)


class Frame:
    """``width`` x ``height`` RGB pixels, row-major from the top left.

    Every drawing call clips silently: pixels outside the frame are ignored.
    """

    __slots__ = ("height", "pixels", "width")

    def __init__(self, width: int, height: int, fill: RGB = BLACK) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("frame size must be positive")
        self.width = width
        self.height = height
        self.pixels: list[RGB] = [fill] * (width * height)

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def copy(self) -> Frame:
        f = Frame(self.width, self.height)
        f.pixels = list(self.pixels)
        return f

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Frame)
            and self.size == other.size
            and self.pixels == other.pixels
        )

    def __repr__(self) -> str:
        return f"Frame({self.width}x{self.height})"

    # --- primitives -------------------------------------------------------------------

    def get(self, x: int, y: int) -> RGB:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x}, {y}) is outside {self.width}x{self.height}")
        return self.pixels[y * self.width + x]

    def pixel(self, x: int, y: int, color: RGB) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y * self.width + x] = color

    def fill(self, color: RGB) -> None:
        self.pixels = [color] * (self.width * self.height)

    def rect(self, x: int, y: int, w: int, h: int, color: RGB, *, filled: bool = True) -> None:
        """A filled rectangle, or a 1-pixel outline with ``filled=False``."""
        if w <= 0 or h <= 0:
            return
        if filled:
            for yy in range(max(y, 0), min(y + h, self.height)):
                for xx in range(max(x, 0), min(x + w, self.width)):
                    self.pixels[yy * self.width + xx] = color
        else:
            self.hline(x, y, w, color)
            self.hline(x, y + h - 1, w, color)
            self.vline(x, y, h, color)
            self.vline(x + w - 1, y, h, color)

    def hline(self, x: int, y: int, w: int, color: RGB) -> None:
        for xx in range(x, x + w):
            self.pixel(xx, y, color)

    def vline(self, x: int, y: int, h: int, color: RGB) -> None:
        for yy in range(y, y + h):
            self.pixel(x, yy, color)

    def blit(
        self,
        src: Frame | Iterable[Iterable[RGB | None]],
        x: int,
        y: int,
        *,
        transparent: RGB | None = None,
    ) -> None:
        """Copy ``src`` (a Frame, or rows of colours where ``None`` is transparent) to (x, y).

        With a Frame source, pixels equal to ``transparent`` are skipped.
        """
        if isinstance(src, Frame):
            for sy in range(src.height):
                for sx in range(src.width):
                    c = src.pixels[sy * src.width + sx]
                    if transparent is not None and c == transparent:
                        continue
                    self.pixel(x + sx, y + sy, c)
            return
        for sy, row in enumerate(src):
            for sx, c in enumerate(row):
                if c is not None:
                    self.pixel(x + sx, y + sy, c)

    def text(
        self,
        x: int,
        y: int,
        s: str,
        color: RGB,
        font: Font | None = None,
        *,
        scale: int = 1,
    ) -> int:
        """Draw ``s`` with its top-left at (x, y). Returns the advance width in pixels."""
        if font is None:
            from arthur.render.font import small_font

            font = small_font()
        return font.draw(self, x, y, s, color, scale=scale)

    # --- encoders ---------------------------------------------------------------------

    def to_rgb888(self) -> bytes:
        """``width*height*3`` bytes, red-green-blue per pixel, row-major (AWTRIX NG bitmap)."""
        out = bytearray(len(self.pixels) * 3)
        for i, (r, g, b) in enumerate(self.pixels):
            out[3 * i] = r
            out[3 * i + 1] = g
            out[3 * i + 2] = b
        return bytes(out)

    def to_ints(self) -> list[int]:
        """Packed ``0xRRGGBB`` per pixel, row-major (the shape of GET /api/v1/display/screen)."""
        return [(r << 16) | (g << 8) | b for r, g, b in self.pixels]

    @classmethod
    def from_ints(cls, width: int, height: int, ints: Iterable[int]) -> Frame:
        f = cls(width, height)
        px = [((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF) for n in ints]
        if len(px) != width * height:
            raise ValueError(f"expected {width * height} pixels, got {len(px)}")
        f.pixels = px
        return f

    def to_ascii(self, legend: Mapping[RGB, str] | None = None, *, off: str = ".") -> str:
        """One character per pixel, one line per row.

        ``legend`` maps colours to characters. Black is ``off``. Colours not in the legend get
        ``?``.
        """
        legend = dict(legend or {})
        legend.setdefault(BLACK, off)
        lines = []
        for y in range(self.height):
            row = self.pixels[y * self.width : (y + 1) * self.width]
            lines.append("".join(legend.get(c, "?") for c in row))
        return "\n".join(lines)

    def to_png(self, scale: int = 1, *, gap: int = 0) -> bytes:
        """An RGB PNG, each pixel ``scale`` x ``scale``; ``gap`` dark pixels between LEDs."""
        if scale < 1:
            raise ValueError("scale must be >= 1")
        gap = max(0, min(gap, scale - 1))
        w, h = self.width * scale, self.height * scale
        raw = bytearray()
        for y in range(self.height):
            for sy in range(scale):
                raw.append(0)  # filter: none
                in_gap_row = sy >= scale - gap
                for x in range(self.width):
                    c = self.pixels[y * self.width + x]
                    for sx in range(scale):
                        if in_gap_row or sx >= scale - gap:
                            raw += b"\x10\x10\x10"
                        else:
                            raw += bytes(c)

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def overlay_stale(frame: Frame, color: RGB = (0x6E, 0x07, 0x00)) -> Frame:
    """A copy of ``frame`` with the clock's 1-pixel out-of-date border drawn on it."""
    f = frame.copy()
    f.rect(0, 0, f.width, f.height, color, filled=False)
    return f
