"""Text that must fit its box, and the audit that proves it does.

Every label a screen draws goes through :func:`put_text`, which is told the last column the
text may use. Text that is too wide is never silently trimmed:

- normally it is drawn clipped to its box (so it can never spill into a neighbour) and the
  overflow is remembered in :data:`OVERFLOWS`, so a caller can log it;
- inside :func:`strict_text` (the tests switch this on) it raises :class:`TextFitError`.

In strict mode :func:`check_text` also proves that nothing else is drawn on or right next to
any text: every lit pixel within one pixel of a label's ink must be ink of some label. That is
what keeps gauges from reading as underlines and lines from running through words.

The one deliberate exception is :func:`draw_behind_text`: something drawn *behind* the labels
(today only idle Arthur's tail swinging through the bottom line). Inside the label's box the
letters hide it: where it crosses ink the ink changes to a tint, and the gaps stay dark, so
every letter keeps its shape. Outside the box its pixels may sit against the label. :func:`check_text` exempts exactly the pixels it
drew and nothing else.

Screens pick between wordings with :func:`first_fitting`, never by cutting letters off.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from arthur.render.font import Font, small_font
from arthur.render.frame import RGB, Frame


class TextFitError(AssertionError):
    """A label does not fit its box, or something is drawn on or against a label."""


@dataclass(frozen=True)
class TextRecord:
    text: str
    x: int
    y: int
    right: int
    ink: frozenset[tuple[int, int]]
    frame_id: int


@dataclass(frozen=True)
class BehindRecord:
    """Pixels drawn by :func:`draw_behind_text` into one frame."""

    cells: frozenset[tuple[int, int]]
    frame_id: int


_strict: ContextVar[list[TextRecord] | None] = ContextVar("arthur_text_strict", default=None)
_behind: ContextVar[list[BehindRecord] | None] = ContextVar("arthur_text_behind", default=None)

#: Labels that did not fit, in non-strict mode: (text, needed width, box width). Bounded.
OVERFLOWS: list[tuple[str, int, int]] = []


@contextmanager
def strict_text() -> Iterator[list[TextRecord]]:
    """Inside this block, overflowing text raises and every label is recorded."""
    records: list[TextRecord] = []
    token = _strict.set(records)
    token_b = _behind.set([])
    try:
        yield records
    finally:
        _behind.reset(token_b)
        _strict.reset(token)


def is_strict() -> bool:
    return _strict.get() is not None


def text_width(s: str, font: Font | None = None, scale: int = 1) -> int:
    return (font or small_font()).width(s, scale)


def fits(s: str, width: int, font: Font | None = None, scale: int = 1) -> bool:
    return text_width(s, font, scale) <= width


def first_fitting(
    options: Iterable[str], width: int, font: Font | None = None, scale: int = 1
) -> str:
    """The first of ``options`` that fits ``width`` pixels. The last option is the fallback
    and is returned even if it does not fit (put_text then reports it)."""
    last = ""
    for s in options:
        last = s
        if fits(s, width, font, scale):
            return s
    return last


def put_text(
    f: Frame,
    x: int,
    y: int,
    s: str,
    color: RGB,
    font: Font | None = None,
    *,
    right: int,
    scale: int = 1,
) -> int:
    """Draw ``s`` with the top of its capitals at row ``y``, using columns ``x..right``.

    Returns the ink width. Overflow raises in strict mode; otherwise the text is clipped at
    ``right`` and the overflow remembered.
    """
    font = font or small_font()
    need = font.width(s, scale)
    have = right - x + 1
    if need > have:
        if is_strict():
            raise TextFitError(f"{s!r} needs {need}px but its box at x={x} has {have}px")
        if len(OVERFLOWS) < 100:
            OVERFLOWS.append((s, need, have))
    scratch = Frame(f.width, f.height)
    font.draw(scratch, x, y, s, (255, 255, 255), scale=scale)
    ink = set()
    for i, p in enumerate(scratch.pixels):
        if p != (0, 0, 0):
            px, py = i % f.width, i // f.width
            if px <= right:
                ink.add((px, py))
                f.pixel(px, py, color)
    records = _strict.get()
    if records is not None:
        records.append(TextRecord(s, x, y, right, frozenset(ink), id(f)))
    return min(need, have)


def draw_behind_text(
    f: Frame,
    cells: Iterable[tuple[int, int, RGB]],
    tint: RGB,
    *,
    box: tuple[int, int, int, int],
) -> set[tuple[int, int]]:
    """Draw ``cells`` (x, y, colour) as if passing behind the label occupying ``box``
    (x0, y0, x1, y1: the label's own ink box).

    Call it after the label is drawn. Inside ``box`` the label hides what passes behind it:
    a cell on a letter's ink turns that pixel ``tint`` (the letter stays whole, in another
    colour) and a cell on an empty pixel is not drawn, so no stray pixel changes a letter's
    shape. Outside ``box`` every cell is drawn in its own colour. Returns the tinted pixels.

    This is the one allowed exception to "nothing touches a label". In strict mode a lit
    pixel in ``box`` that is not label ink raises, and :func:`check_text` exempts exactly the
    pixels drawn here.
    """
    x0, y0, x1, y1 = box
    off = (0, 0, 0)
    records = _strict.get()
    ink: set[tuple[int, int]] | None = None
    if records is not None:
        ink = set()
        for r in records:
            if r.frame_id == id(f):
                ink |= r.ink
    tinted: set[tuple[int, int]] = set()
    drawn: set[tuple[int, int]] = set()
    for x, y, color in cells:
        if not (0 <= x < f.width and 0 <= y < f.height):
            continue
        if x0 <= x <= x1 and y0 <= y <= y1:
            if f.get(x, y) == off:
                continue  # hidden behind the label
            if ink is not None and (x, y) not in ink:
                raise TextFitError(f"draw_behind_text: ({x},{y}) in its box is not label ink")
            f.pixel(x, y, tint)
            tinted.add((x, y))
        else:
            f.pixel(x, y, color)
        drawn.add((x, y))
    behind = _behind.get()
    if behind is not None:
        behind.append(BehindRecord(frozenset(drawn), id(f)))
    return tinted


def check_text(frame: Frame, records: Sequence[TextRecord]) -> None:
    """Raise :class:`TextFitError` if any lit pixel touches a label without being label ink.

    ``records`` are the labels drawn into ``frame`` (others are ignored). A pixel "touches" a
    label when it is inside the label's ink bounding box grown by one pixel. Pixels drawn by
    :func:`draw_behind_text` into this frame are exempt (that is its whole point); label ink
    it tinted is still ink.
    """
    mine = [r for r in records if r.frame_id == id(frame)]
    if not mine:
        return
    all_ink: set[tuple[int, int]] = set()
    for r in mine:
        all_ink |= r.ink
    for b in _behind.get() or ():
        if b.frame_id == id(frame):
            all_ink |= b.cells
    off = (0, 0, 0)
    for r in mine:
        if not r.ink:
            continue
        xs = [p[0] for p in r.ink]
        ys = [p[1] for p in r.ink]
        for yy in range(max(0, min(ys) - 1), min(frame.height, max(ys) + 2)):
            for xx in range(max(0, min(xs) - 1), min(frame.width, max(xs) + 2)):
                if (xx, yy) in all_ink:
                    continue
                if frame.get(xx, yy) != off:
                    raise TextFitError(
                        f"pixel ({xx},{yy}) is drawn on or against the label {r.text!r}"
                    )
    # Ink can be overdrawn by something later; that is a collision too.
    for r in mine:
        for px, py in r.ink:
            if frame.get(px, py) == off:
                raise TextFitError(f"the label {r.text!r} was drawn over at ({px},{py})")
