"""A minimal BDF bitmap-font reader and text drawing.

The fonts in ``arthur/render/fonts/`` are vendored from AWTRIX NG v1.2.3 under the SIL Open
Font License 1.1; see ``fonts/NOTICE.md``.

- :func:`small_font`: ``awtrix.bdf`` (AWTRIX NG's ``small`` base): 5-pixel capitals, 4-pixel
  advance, 2-pixel space.
- :func:`large_font`: ``MatrixChunky8.bdf`` (AWTRIX NG's ``large`` base): 7-pixel capitals,
  4-pixel advance.
- :func:`condensed_small_font`: the small font with 3-pixel-wide M and W (drawn here, not
  vendored), for a line that is a couple of pixels too long in the plain small font.

Text is positioned by the top row of its capitals.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arthur.render.frame import RGB, Frame

FONT_DIR = Path(__file__).resolve().parent / "fonts"


@dataclass(frozen=True)
class Glyph:
    width: int  # BBX width
    height: int  # BBX height
    xoff: int
    yoff: int  # bottom of the bitmap relative to the baseline (up is positive)
    advance: int  # DWIDTH x
    rows: tuple[int, ...]  # one int per row, bit (width-1-x) is pixel x

    def on(self, x: int, y: int) -> bool:
        return bool(self.rows[y] >> (self.width - 1 - x) & 1)


class Font:
    def __init__(self, name: str, glyphs: dict[int, Glyph], ascent: int, descent: int) -> None:
        self.name = name
        self.glyphs = glyphs
        self.ascent = ascent
        self.descent = descent
        ref = glyphs.get(ord("A")) or next(iter(glyphs.values()))
        #: Rows above the baseline taken by a capital letter.
        self.cap_height = ref.yoff + ref.height

    def __repr__(self) -> str:
        return f"Font({self.name!r}, cap_height={self.cap_height})"

    def glyph(self, ch: str) -> Glyph:
        g = self.glyphs.get(ord(ch))
        if g is None and ch.upper() != ch:
            g = self.glyphs.get(ord(ch.upper()))
        if g is None:
            g = self.glyphs[ord("?")]
        return g

    def advance(self, s: str, scale: int = 1) -> int:
        """Total advance of ``s`` (including the spacing after the last glyph)."""
        return sum(self.glyph(c).advance for c in s) * scale

    def width(self, s: str, scale: int = 1) -> int:
        """Pixels from the left edge of the first glyph to the right edge of the last ink."""
        if not s:
            return 0
        adv = 0
        right = 0
        for c in s:
            g = self.glyph(c)
            if g.width and any(g.rows):
                right = max(right, adv + g.xoff + g.width)
            adv += g.advance
        return right * scale

    def draw(self, frame: Frame, x: int, y: int, s: str, color: RGB, *, scale: int = 1) -> int:
        """Draw ``s`` with the top of its capitals at row ``y``. Returns the advance."""
        pen = x
        for c in s:
            g = self.glyph(c)
            top = y + (self.cap_height - g.yoff - g.height) * scale
            left = pen + g.xoff * scale
            for gy in range(g.height):
                for gx in range(g.width):
                    if g.on(gx, gy):
                        if scale == 1:
                            frame.pixel(left + gx, top + gy, color)
                        else:
                            frame.rect(left + gx * scale, top + gy * scale, scale, scale, color)
            pen += g.advance * scale
        return pen - x


def parse_bdf(text: str, name: str = "bdf") -> Font:
    glyphs: dict[int, Glyph] = {}
    ascent = descent = 0
    lines = iter(text.splitlines())
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        if key == "FONT_ASCENT":
            ascent = int(parts[1])
        elif key == "FONT_DESCENT":
            descent = int(parts[1])
        elif key == "STARTCHAR":
            enc = -1
            adv = 0
            bbx = (0, 0, 0, 0)
            rows: list[int] = []
            for inner in lines:
                ip = inner.split()
                if not ip:
                    continue
                if ip[0] == "ENCODING":
                    enc = int(ip[1])
                elif ip[0] == "DWIDTH":
                    adv = int(ip[1])
                elif ip[0] == "BBX":
                    bbx = (int(ip[1]), int(ip[2]), int(ip[3]), int(ip[4]))
                elif ip[0] == "BITMAP":
                    w = bbx[0]
                    nbytes = (w + 7) // 8
                    for _ in range(bbx[1]):
                        hexrow = next(lines).strip()
                        v = int(hexrow, 16) if hexrow else 0
                        rows.append(v >> (nbytes * 8 - w) if w else 0)
                elif ip[0] == "ENDCHAR":
                    break
            if enc >= 0:
                glyphs[enc] = Glyph(bbx[0], bbx[1], bbx[2], bbx[3], adv, tuple(rows))
    if not glyphs:
        raise ValueError(f"no glyphs in {name}")
    return Font(name, glyphs, ascent, descent)


def load_bdf(path: str | Path) -> Font:
    p = Path(path)
    return parse_bdf(p.read_text(encoding="ascii", errors="replace"), name=p.stem)


@cache
def small_font() -> Font:
    return load_bdf(FONT_DIR / "awtrix.bdf")


@cache
def large_font() -> Font:
    return load_bdf(FONT_DIR / "MatrixChunky8.bdf")


#: Narrow M and W for :func:`condensed_small_font`: 3 pixels wide, 5 rows, 4-pixel advance.
CONDENSED_GLYPHS: dict[str, tuple[str, ...]] = {
    "M": ("101", "111", "111", "101", "101"),
    "W": ("101", "101", "111", "111", "101"),
}


@cache
def condensed_small_font() -> Font:
    """The small font with 3-pixel-wide M and W, so a line saves 2 px per M or W."""
    base = small_font()
    glyphs = dict(base.glyphs)
    for ch, art in CONDENSED_GLYPHS.items():
        ref = base.glyph(ch)
        rows = tuple(int(r, 2) for r in art)
        glyphs[ord(ch)] = Glyph(3, len(rows), 0, ref.yoff, 4, rows)
    return Font(base.name + "-condensed", glyphs, base.ascent, base.descent)
