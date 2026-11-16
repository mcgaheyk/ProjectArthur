"""Text-art icons: ``icons/<name>.<size>.txt``, one character per pixel.

Characters are palette-role codes from ``[palette_chars]`` in config/display.toml, plus:

- ``.`` transparent (not drawn),
- ``*`` the accent role the screen picks when drawing,
- ``%`` a level-fill cell: filled from the bottom up to a given fraction, in the fill role.

Lines starting with ``#`` are comments. A missing 16x16 icon falls back to its 8x8 art drawn
at double size.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from arthur.config import Config
from arthur.render.frame import Frame

ICON_DIR = Path(__file__).resolve().parent / "icons"


@dataclass(frozen=True)
class Icon:
    name: str
    size: int
    rows: tuple[str, ...]


@cache
def _load(name: str, size: int) -> Icon | None:
    p = ICON_DIR / f"{name}.{size}.txt"
    if not p.exists():
        return None
    rows = tuple(
        line.rstrip("\n")
        for line in p.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    if len(rows) != size or any(len(r) != size for r in rows):
        raise ValueError(f"{p.name}: expected {size}x{size} characters")
    return Icon(name, size, rows)


def icon_names() -> list[str]:
    return sorted({p.name.split(".")[0] for p in ICON_DIR.glob("*.txt")})


def get_icon(name: str, size: int) -> tuple[Icon, int]:
    """The icon art and the scale to draw it at to fill ``size``."""
    icon = _load(name, size)
    if icon is not None:
        return icon, 1
    if size == 16:
        small = _load(name, 8)
        if small is not None:
            return small, 2
    raise KeyError(f"no icon {name!r} at {size}x{size}")


def draw_icon(
    frame: Frame,
    name: str,
    x: int,
    y: int,
    cfg: Config,
    *,
    size: int = 8,
    accent: str = "text",
    fill: float = 0.0,
    fill_role: str | None = None,
    override: str | None = None,
) -> None:
    """Draw icon ``name`` with its top-left at (x, y).

    ``accent`` is the role for ``*``. ``fill`` (0..1) is how much of the ``%`` area is lit, from
    the bottom, in ``fill_role`` (default: the accent). ``override`` draws every lit pixel in
    one role (used for blinking or monochrome variants).
    """
    icon, scale = get_icon(name, size)
    pal = cfg.palette
    char_role = {c: r for r, c in pal.chars.items()}
    off_char = pal.chars.get("off", ".")

    fill_rows = [i for i, r in enumerate(icon.rows) if "%" in r]
    lit_fill: set[int] = set()
    if fill_rows and fill > 0:
        n = len(fill_rows)
        k = max(1, round(min(fill, 1.0) * n))
        lit_fill = set(fill_rows[n - k :])

    for iy, row in enumerate(icon.rows):
        for ix, ch in enumerate(row):
            if ch == "." or ch == off_char:
                continue
            if ch == "%":
                if iy not in lit_fill:
                    continue
                role = fill_role or accent
            elif ch == "*":
                role = accent
            else:
                role = char_role.get(ch)
                if role is None:
                    raise ValueError(f"icon {icon.name}: unknown character {ch!r}")
            if override is not None:
                role = override
            color = pal.rgb(role)
            if scale == 1:
                frame.pixel(x + ix, y + iy, color)
            else:
                frame.rect(x + ix * scale, y + iy * scale, scale, scale, color)
