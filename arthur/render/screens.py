"""Layouts: DisplayState + now + config → Frame, at 32x8 and 52x16.

A pure function of its inputs; knows nothing about AWTRIX. Every display kind has at least one
layout at both sizes; some have named variants. The first variant registered for a
(size, kind) is the default.

Arthur (an orange tabby) is the mascot; see design-screen section 0 for the direction.

Canvas rules (docs/states.md, design-screen skill):

- The clock's stale border covers row 0, the last row, column 0 and the last column, so
  nothing is drawn there. One narrow exception: a 32x8 gauge on the bottom row
  (:data:`BOTTOM_GAUGE_32`).
- Every label goes through :func:`arthur.render.text.put_text` with the box it must fit, and
  nothing else may touch a label (the strict text check in the tests).
- 32x8: 8x8 icon at x=0, text from x=9 to x=28 with capitals on rows 1-5, badges on column 30.
- 52x16: 16x16 picture at x=0, content in x=17..47, badges in columns 49-50.
- About one frame a second: animation phases are derived from ``now``. Live states change at
  most once a second; idle almost never (a blink or tail flick every
  ``idle_blink_every_s``).
- Captions come from ``[captions]`` in the config: one per visit, picked from when the state
  began; a caption that does not fit its line is skipped, never cut.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from arthur.config import Config, parse_size
from arthur.model import KINDS, DisplayState
from arthur.render.font import Font, large_font, small_font
from arthur.render.frame import Frame
from arthur.render.icons import draw_art, draw_icon, icon_cells
from arthur.render.text import check_text, first_fitting, fits, is_strict, put_text, strict_text

Size = tuple[int, int]
Layout = Callable[[DisplayState, datetime, Config], Frame]

S32: Size = (32, 8)
S52: Size = (52, 16)

# 32x8 geometry
T32_X = 9  # text starts here (after the 8x8 icon and a 1-px gap)
T32_R = 28  # last column text may use (29 is a gap, 30 holds badges, 31 is the border)
T32_W = T32_R - T32_X + 1
T32_Y = 1  # top row of 5-px capitals
G32_Y = 7  # gauge row: the bottom row, under a 1-px gap (see BOTTOM_GAUGE_32)

# 52x16 geometry
C52_X = 17  # content starts here (after the 16x16 picture and a 1-px gap)
C52_R = 47  # last content column (48 is a gap, 49-50 hold badges, 51 is the border)
C52_W = C52_R - C52_X + 1
L1_Y = 1  # first line: big font rows 1-7, or small font rows 1-5
L2_Y = 9  # second line: small font rows 9-13
GAUGE52_Y = 13  # the drawer gauge live screens share: rows 13-14

_REGISTRY: dict[tuple[Size, str], dict[str, Layout]] = {}

#: 32x8 (kind, variant) layouts that draw a gauge on the bottom row. That row is under the
#: clock's stale border, which is accepted for a gauge: losing it while data is stale is fine,
#: and it keeps a 1-px gap between the gauge and the text so it does not read as an underline.
#: Nothing else may use the outer rows or columns.
BOTTOM_GAUGE_32: set[tuple[str, str]] = set()


def layout(
    size: Size, kind: str, variant: str = "default", *, bottom_gauge: bool = False
) -> Callable[[Layout], Layout]:
    """Register a layout function for (size, kind) under ``variant``."""

    def deco(fn: Layout) -> Layout:
        _REGISTRY.setdefault((size, kind), {})[variant] = fn
        if bottom_gauge:
            assert size == S32, "only 32x8 layouts may put a gauge on the bottom row"
            BOTTOM_GAUGE_32.add((kind, variant))
        return fn

    return deco


# --- time --------------------------------------------------------------------------------


def tick(now: datetime) -> int:
    """Whole seconds since the epoch: the clock every animation runs on."""
    return math.floor(now.timestamp())


def blink(now: datetime) -> bool:
    """True on even seconds: drives 2-frame blinks at about one frame a second."""
    return tick(now) % 2 == 0


def phase(now: datetime, n: int, every_s: float = 1) -> int:
    """Which of ``n`` animation frames shows at ``now``, each held ``every_s`` seconds."""
    return int(tick(now) // max(1, every_s)) % n


def seconds_between(a: datetime | None, b: datetime) -> float | None:
    return None if a is None else (b - a).total_seconds()


def fmt_clock(seconds: float | None) -> str:
    """A timer: ``m:ss`` below ten minutes, then ``12M``, ``3H``, ``2D``."""
    if seconds is None:
        return "--"
    s = max(0, int(seconds))
    if s < 600:
        return f"{s // 60}:{s % 60:02d}"
    if s < 100 * 60:
        return f"{s // 60}M"
    if s < 100 * 3600:
        return f"{s // 3600}H"
    return f"{s // 86400}D"


def fmt_countdown(seconds: float | None) -> str:
    """Time left, rounded up so it reaches 0:00 only at the deadline."""
    if seconds is None:
        return "--"
    return fmt_clock(math.ceil(max(0.0, seconds)))


def fmt_age(seconds: float | None) -> str:
    """How long ago: ``NOW``, ``5M``, ``3H``, ``2D``."""
    if seconds is None:
        return "?"
    s = max(0, int(seconds))
    if s < 60:
        return "NOW"
    if s < 3600:
        return f"{s // 60}M"
    if s < 48 * 3600:
        return f"{s // 3600}H"
    return f"{s // 86400}D"


def fmt_pct(v: float | None) -> str:
    return "--%" if v is None else f"{round(max(0.0, min(100.0, v)))}%"


# --- levels ------------------------------------------------------------------------------


def drawer_frac(state: DisplayState) -> float:
    return max(0.0, min(1.0, (state.drawer_pct or 0) / 100))


def litter_frac(state: DisplayState) -> float:
    return max(0.0, min(1.0, (state.litter_pct or 0) / 100))


def drawer_role(state: DisplayState, cfg: Config) -> str:
    """Green with room, amber filling, red nearly full or full."""
    pct = state.drawer_pct or 0
    if state.kind == "drawer_full" or pct >= cfg.thresholds.drawer_warn_pct:
        return "alert"
    if pct >= cfg.thresholds.drawer_amber_pct:
        return "attention"
    return "ok"


def litter_role(state: DisplayState) -> str:
    """Sand-coloured, red when the robot says the litter is low."""
    st = (state.litter_state or "").upper()
    return "alert" if st in ("LOW", "EMPTY") else "sand"


def gauge(f: Frame, x: int, y: int, w: int, h: int, frac: float, role: str, cfg: Config) -> None:
    """A horizontal bar: ``frac`` of it lit in ``role`` over a dim track."""
    frac = max(0.0, min(1.0, frac))
    f.rect(x, y, w, h, cfg.rgb("dim"))
    lit = round(frac * w)
    if frac > 0 and lit == 0:
        lit = 1
    f.rect(x, y, lit, h, cfg.rgb(role))


def vgauge(f: Frame, x: int, y: int, w: int, h: int, frac: float, role: str, cfg: Config) -> None:
    """A vertical bar filled from the bottom."""
    frac = max(0.0, min(1.0, frac))
    f.rect(x, y, w, h, cfg.rgb("dim"))
    lit = round(frac * h)
    if frac > 0 and lit == 0:
        lit = 1
    f.rect(x, y + h - lit, w, lit, cfg.rgb(role))


def cycle_fraction(state: DisplayState, now: datetime) -> float:
    if state.cycle_start is None or state.cycle_end is None:
        return 0.0
    total = (state.cycle_end - state.cycle_start).total_seconds()
    done = (now - state.cycle_start).total_seconds()
    return 0.0 if total <= 0 else max(0.0, min(0.99, done / total))


def wait_fraction_left(state: DisplayState, now: datetime) -> float:
    if state.since is None or state.deadline is None:
        return 1.0
    total = (state.deadline - state.since).total_seconds()
    left = (state.deadline - now).total_seconds()
    return 0.0 if total <= 0 else max(0.0, min(1.0, left / total))


# --- captions ----------------------------------------------------------------------------

#: Every place a caption key is drawn: key → {(size, box width, font, scale)}. Filled as
#: screens render; the tests use it to prove every configured caption fits where it is used.
CAPTION_USES: dict[str, set[tuple[Size, int, Font, int]]] = {}


def caption(
    state: DisplayState,
    cfg: Config,
    key: str,
    size: Size,
    width: int,
    font: Font,
    *,
    scale: int = 1,
    fallback: str,
) -> str:
    """One of the ``[captions] key`` jokes that fits ``width``, fixed for the visit.

    The pick is a hash of when the state began, so it stays put while the state lasts and
    varies between visits. Captions too wide for the line are skipped; with none left (or no
    such key) the plain ``fallback`` is used.
    """
    CAPTION_USES.setdefault(key, set()).add((size, width, font, scale))
    options = [c for c in cfg.captions.get(key, ()) if fits(c, width, font, scale)]
    if not options:
        return fallback
    began = state.since or state.last_seen
    if began is None:
        return options[0]
    i = zlib.crc32(f"{key}|{math.floor(began.timestamp())}".encode()) % len(options)
    return options[i]


# --- drawing helpers ---------------------------------------------------------------------


def _f32() -> Frame:
    return Frame(*S32)


def _f52() -> Frame:
    return Frame(*S52)


def icon32(f: Frame, cfg: Config, name: str, accent: str = "text") -> None:
    draw_icon(f, name, 0, 0, cfg, accent=accent)


def icon52(f: Frame, cfg: Config, name: str, accent: str = "text") -> None:
    draw_icon(f, name, 0, 0, cfg, size=16, accent=accent)


def text32(f: Frame, cfg: Config, s: str, role: str, x: int = T32_X) -> None:
    put_text(f, x, T32_Y, s, cfg.rgb(role), small_font(), right=T32_R)


def big52(f: Frame, cfg: Config, s: str, role: str, y: int = L1_Y, x: int = C52_X) -> None:
    """A line in the big font (7 rows from ``y``)."""
    put_text(f, x, y, s, cfg.rgb(role), large_font(), right=C52_R)


def small52(f: Frame, cfg: Config, s: str, role: str, y: int = L2_Y, x: int = C52_X) -> None:
    """A line in the small font (5 rows from ``y``)."""
    put_text(f, x, y, s, cfg.rgb(role), small_font(), right=C52_R)


def digits52(f: Frame, cfg: Config, s: str, role: str) -> None:
    """Double-size small-font digits on rows 1-10."""
    put_text(f, C52_X, 1, s, cfg.rgb(role), small_font(), right=C52_R, scale=2)


def drawer_gauge52(f: Frame, state: DisplayState, cfg: Config) -> None:
    """The drawer gauge live screens share at 52x16: rows 13-14, full content width."""
    gauge(f, C52_X, GAUGE52_Y, C52_W, 2, drawer_frac(state), drawer_role(state, cfg), cfg)


def bottom_gauge32(f: Frame, frac: float, role: str, cfg: Config) -> None:
    """A 32x8 gauge on the bottom row; only for layouts registered with bottom_gauge."""
    gauge(f, T32_X, G32_Y, T32_W, 1, frac, role, cfg)


def icon_rising(
    f: Frame, cfg: Config, name: str, x: int, y: int, size: int, drop: int, floor: int
) -> None:
    """Draw icon ``name`` ``drop`` rows lower than (x, y), cut off below row ``floor``: it
    looks like it is rising out of the ground."""
    scratch = Frame(f.width, f.height)
    draw_icon(scratch, name, x, y + drop, cfg, size=size)
    off = cfg.rgb("off")
    for yy in range(min(floor + 1, f.height)):
        for xx in range(f.width):
            c = scratch.pixels[yy * f.width + xx]
            if c != off:
                f.pixel(xx, yy, c)


# Tiny marks that label the idle gauges.
BIN_MARK = ("..B..", ".BHB.", "BBBBB")  # a little poop: the waste drawer
LITTER_MARK = ("s.s.s", ".s.s.", "sssss")  # a little sand pile: the litter
BIN_MARK3 = (".B.", "BHB", "BBB")
LITTER_MARK3 = ("s.s", ".s.", "sss")
#: A 3x3 "z" for the sleeping moon.
Z_ART = ("CCC", ".C.", "CCC")
#: Stink lines beside the poop: two wiggle frames.
STINK_A = (".K..K", "K..K.", ".K..K", "K..K.", ".K..K")
STINK_B = ("K..K.", ".K..K", "K..K.", ".K..K", "K..K.")


# =========================================================================================
# Rare states: one template (icon, headline, a line saying what is going on)
# =========================================================================================


@dataclass(frozen=True)
class Rare:
    icon: str
    accent: str  # the icon's accent role
    headline: str  # 52x16 big line
    role: str  # headline colour (and the 32x8 word colour)
    line2: str  # 52x16 small line, plain words
    pages: tuple[str, ...]  # 32x8: one word at a time, every page_s
    blink_icon: bool = False


_NO_DATA_REASON: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # reason → (52x16 second-line options, 32x8 pages after "NO" "DATA")
    "auth": (("LOGIN",), ("LOGIN",)),
    "api": (("API ERROR", "API ERR"), ("API", "ERROR")),
    "no_robots": (("NO ROBOT",), ("NO", "ROBOT")),
    "timeout": (("TIMEOUT",), ("TIME", "OUT")),
    "no_snapshot": (("WAITING",), ("WAIT",)),
}

#: Fault code → (52x16 second-line options, 32x8 pages after "FAULT").
FAULT_WORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "PD": (("PINCH",), ("PINCH",)),
    "OTF": (("JAMMED",), ("JAM",)),
    "CSF": (("CAT SENSOR", "SENSOR"), ("CAT", "SENSOR")),
}


def rare_for(state: DisplayState, now: datetime) -> Rare:
    sm = small_font()
    kind = state.kind
    if kind == "no_data":
        line2s, pages = _NO_DATA_REASON.get(state.detail or "", (("WHISKER",), ("WHISKER",)))
        return Rare("ghost_q", "text", "NO DATA", "alert", first_fitting(line2s, C52_W, sm),
                    ("NO", "DATA", *pages))
    if kind == "robot_offline":
        age = fmt_age(seconds_between(state.last_seen, now))
        return Rare("wifi_x", "alert", "OFFLINE", "alert",
                    first_fitting([f"SEEN {age}", f"{age} AGO", age], C52_W, sm),
                    ("OFF", "LINE", age, "AGO"))
    if kind == "robot_off":
        return Rare("power", "attention", "ROBOT", "attention", "IS OFF", ("ROBOT", "IS", "OFF"))
    if kind == "fault":
        code = state.fault_code or "?"
        line2s, pages = FAULT_WORDS.get(code, ((f"CODE {code}", code), (code,)))
        return Rare("warning", "alert", "FAULT", "alert", first_fitting(line2s, C52_W, sm),
                    ("FAULT", *pages), blink_icon=True)
    if kind == "bonnet_removed":
        return Rare("bonnet", "attention", "BONNET", "attention", "IS OFF", ("LID", "OFF"))
    if kind == "paused":
        t = fmt_clock(seconds_between(state.since, now))
        return Rare("pause", "attention", "PAUSED", "attention", t, ("PAUSE", t))
    if kind == "emptying":
        return Rare("empty", "gold", "EMPTY", "gold", "CYCLE", ("DUMP", "CYCLE"))
    if kind == "powering":
        if state.detail == "down":
            return Rare("power", "gold", "POWER", "gold", "BYE BYE", ("SHUT", "DOWN"),
                        blink_icon=True)
        return Rare("power", "gold", "POWER", "gold", "STARTING", ("START", "UP"),
                    blink_icon=True)
    raise KeyError(kind)


def _rare_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    r = rare_for(state, now)
    f = _f52()
    if not r.blink_icon or blink(now):
        icon52(f, cfg, r.icon, r.accent)
    big52(f, cfg, r.headline, r.role)
    small52(f, cfg, r.line2, "text")
    return f


def _rare_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    """Icon plus one word at a time. A word too wide to sit beside the icon gets the whole
    width for its page (and the icon steps aside), rather than being abbreviated."""
    r = rare_for(state, now)
    f = _f32()
    word = r.pages[phase(now, len(r.pages), cfg.timings.page_s)]
    sm = small_font()
    if fits(word, T32_W, sm):
        if not r.blink_icon or blink(now):
            icon32(f, cfg, r.icon, r.accent)
        text32(f, cfg, word, r.role)
    else:
        x = 1 + max(0, T32_R - sm.width(word)) // 2
        put_text(f, x, T32_Y, word, cfg.rgb(r.role), sm, right=T32_R)
    return f


for _kind in ("no_data", "robot_offline", "robot_off", "fault", "bonnet_removed", "paused",
              "emptying", "powering"):
    layout(S32, _kind)(_rare_32)
    layout(S52, _kind)(_rare_52)


# =========================================================================================
# Drawer full: an overflowing pile, red, and it blinks for attention
# =========================================================================================


@layout(S32, "drawer_full")
def _drawer_full_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "pile", "alert" if blink(now) else "dim")
    text32(f, cfg, "FULL", "alert")
    return f


@layout(S52, "drawer_full")
def _drawer_full_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "pile", "alert" if blink(now) else "dim")
    big52(f, cfg, "FULL", "alert")
    sub = caption(state, cfg, "drawer_full", S52, C52_W, small_font(), fallback="EMPTY IT")
    small52(f, cfg, sub, "text")
    return f


# =========================================================================================
# In use: the funny moment. Arthur squats, a poop emoji pops up, stink lines wiggle.
# =========================================================================================

#: In-use loop, one entry per second: (Arthur pose, poop drop in rows (None = hidden), stink).
IN_USE_LOOP: tuple[tuple[str, int | None, int], ...] = (
    ("arthur_squat", 0, 1),
    ("arthur_squat", 0, 2),
    ("arthur_squat", 0, 1),
    ("arthur_squat", None, 0),
    ("arthur_strain", None, 0),
    ("arthur_strain", 4, 0),
)


def _in_use_timer(state: DisplayState, now: datetime) -> str:
    return fmt_clock(seconds_between(state.since, now))


@layout(S52, "in_use", "duo")
def _in_use_duo_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    pose, drop, stink = IN_USE_LOOP[phase(now, len(IN_USE_LOOP))]
    icon52(f, cfg, pose)
    cap = caption(state, cfg, "in_use", S52, C52_W, small_font(), fallback="BUSY")
    small52(f, cfg, cap, "fur", y=L1_Y)
    if drop is not None:
        icon_rising(f, cfg, "poop", C52_X, 7, 8, drop, floor=14)
    if stink:
        draw_art(f, STINK_A if stink == 1 else STINK_B, 26, 9, cfg)
    small52(f, cfg, _in_use_timer(state, now), "text", x=32)
    return f


@layout(S52, "in_use", "door")
def _in_use_door_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "door", "alert")
    small52(f, cfg, "OCCUPIED", "alert", y=L1_Y)
    big52(f, cfg, _in_use_timer(state, now), "text", y=7)
    return f


@layout(S52, "in_use", "timer")
def _in_use_timer_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "arthur_strain" if phase(now, 2) else "arthur_squat")
    digits52(f, cfg, _in_use_timer(state, now), "text")
    drawer_gauge52(f, state, cfg)
    return f


@layout(S32, "in_use", "duo")
def _in_use_duo_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    """Arthur and the poop emoji take turns in the icon slot."""
    f = _f32()
    icon32(f, cfg, ("arthur_strain", "arthur_strain", "poop", "poop")[phase(now, 4)])
    text32(f, cfg, _in_use_timer(state, now), "text")
    return f


@layout(S32, "in_use", "door")
def _in_use_door_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "door", "alert")
    text32(f, cfg, _in_use_timer(state, now), "alert")
    return f


@layout(S32, "in_use", "timer")
def _in_use_timer_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "arthur_strain")
    text32(f, cfg, _in_use_timer(state, now), "text")
    return f


# =========================================================================================
# Waiting: cream hourglass, orange sand, gold digits. Sleeping: a moon and z's.
# =========================================================================================

#: Row of the hourglass neck at each icon size.
_NECK = {16: 7, 8: 3}


def hourglass(f: Frame, cfg: Config, size: int, left: float, now: datetime) -> None:
    """The hourglass with ``left`` (0..1) of the sand still in the top bulb; a grain falls
    through the neck every other second."""
    draw_icon(f, "hourglass", 0, 0, cfg, size=size)
    neck = _NECK[size]
    cells = icon_cells("hourglass", size, "%")
    top = sorted((c for c in cells if c[1] < neck), key=lambda c: -c[1])
    bottom = sorted((c for c in cells if c[1] > neck), key=lambda c: -c[1])
    sand = cfg.rgb("sand")
    for x, y in top[: round(left * len(top))]:
        f.pixel(x, y, sand)
    for x, y in bottom[: round((1 - left) * len(bottom))]:
        f.pixel(x, y, sand)
    if 0 < left < 1 and blink(now):
        for x, y in cells:
            if y == neck:
                f.pixel(x, y, sand)


def _wait_text(state: DisplayState, now: datetime) -> str:
    if state.deadline is None:
        return "WAIT"
    left = (state.deadline - now).total_seconds()
    return "DUE" if left <= 0 else fmt_countdown(left)


def _sleep_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "moon")
    n = phase(now, 4, 2)  # 0..3 z's, a new one every two seconds
    for i, (x, y) in enumerate(((8, 6), (10, 3), (12, 1))):
        if i < n:
            draw_art(f, Z_ART, x, y, cfg)
    head = caption(state, cfg, "waiting_sleep", S52, C52_W, large_font(), fallback="SLEEP")
    big52(f, cfg, head, "cream")
    small52(f, cfg, "NO CYCLE", "text")
    return f


def _sleep_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "moon")
    text32(f, cfg, "Z" * (1 + phase(now, 3, 2)), "cream")
    return f


@layout(S52, "waiting", "countdown")
def _waiting_countdown_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    if state.sleeping:
        return _sleep_52(state, now, cfg)
    f = _f52()
    hourglass(f, cfg, 16, wait_fraction_left(state, now), now)
    digits52(f, cfg, _wait_text(state, now), "gold")
    drawer_gauge52(f, state, cfg)
    return f


@layout(S52, "waiting", "label")
def _waiting_label_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    if state.sleeping:
        return _sleep_52(state, now, cfg)
    f = _f52()
    hourglass(f, cfg, 16, wait_fraction_left(state, now), now)
    cap = caption(state, cfg, "waiting", S52, C52_W, small_font(), fallback="CLEAN IN")
    small52(f, cfg, cap, "sand", y=L1_Y)
    big52(f, cfg, _wait_text(state, now), "gold", y=7)
    return f


@layout(S32, "waiting", "countdown")
def _waiting_countdown_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    if state.sleeping:
        return _sleep_32(state, now, cfg)
    f = _f32()
    hourglass(f, cfg, 8, wait_fraction_left(state, now), now)
    text32(f, cfg, _wait_text(state, now), "gold")
    return f


@layout(S32, "waiting", "bar", bottom_gauge=True)
def _waiting_bar_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    if state.sleeping:
        return _sleep_32(state, now, cfg)
    f = _f32()
    left_frac = wait_fraction_left(state, now)
    hourglass(f, cfg, 8, left_frac, now)
    if state.deadline is None:
        text32(f, cfg, "WAIT", "gold")
        return f
    left = (state.deadline - now).total_seconds()
    text32(f, cfg, "DUE" if left <= 0 else f"{math.ceil(left / 60)}M", "gold")
    bottom_gauge32(f, left_frac, "gold", cfg)
    return f


# =========================================================================================
# Cleaning: the globe turning, in mint
# =========================================================================================


def drum(f: Frame, cfg: Config, size: int, now: datetime) -> None:
    """The robot's globe: a mint ring whose opening turns a step every second, with litter
    lying in the bottom."""
    steps = 8 if size == 16 else 4
    c = (size - 1) / 2
    r = 6.4 if size == 16 else 2.8
    theta = phase(now, steps) * 2 * math.pi / steps - math.pi / 2
    gap = math.radians(38 if size == 16 else 50)
    mint, sand = cfg.rgb("mint"), cfg.rgb("sand")
    for y in range(size):
        for x in range(size):
            dx, dy = x - c, y - c
            d = math.hypot(dx, dy)
            if abs(d - r) <= 0.6:
                diff = (math.atan2(dy, dx) - theta + math.pi) % (2 * math.pi) - math.pi
                if abs(diff) > gap:
                    f.pixel(x, y, mint)
            elif size == 16 and d < r - 1.4 and dy > 1.5:
                f.pixel(x, y, sand)


def _clean_left(state: DisplayState, now: datetime) -> str:
    if state.cycle_end is None:
        return "--"
    return fmt_countdown((state.cycle_end - now).total_seconds())


@layout(S52, "cleaning", "spin")
def _cleaning_spin_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    drum(f, cfg, 16, now)
    big52(f, cfg, _clean_left(state, now), "text")
    cap = caption(state, cfg, "cleaning", S52, C52_W, small_font(), fallback="CLEANING")
    small52(f, cfg, cap, "mint")
    return f


@layout(S52, "cleaning", "gauge")
def _cleaning_gauge_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    drum(f, cfg, 16, now)
    digits52(f, cfg, _clean_left(state, now), "mint")
    drawer_gauge52(f, state, cfg)
    return f


@layout(S32, "cleaning", "countdown")
def _cleaning_countdown_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    drum(f, cfg, 8, now)
    text32(f, cfg, _clean_left(state, now), "mint")
    return f


@layout(S32, "cleaning", "progress", bottom_gauge=True)
def _cleaning_progress_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    drum(f, cfg, 8, now)
    text32(f, cfg, _clean_left(state, now), "mint")
    bottom_gauge32(f, cycle_fraction(state, now), "mint", cfg)
    return f


# =========================================================================================
# Just cleaned: a twinkling sparkle and FRESH!
# =========================================================================================


@layout(S52, "just_cleaned")
def _just_cleaned_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "sparkle_a" if blink(now) else "sparkle_b")
    head = caption(state, cfg, "just_cleaned", S52, C52_W, large_font(), fallback="FRESH!")
    big52(f, cfg, head, "mint")
    drawer_gauge52(f, state, cfg)
    return f


@layout(S32, "just_cleaned")
def _just_cleaned_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "sparkle_a" if blink(now) else "sparkle_b")
    word = caption(state, cfg, "just_cleaned_small", S32, T32_W, small_font(), fallback="FRESH")
    text32(f, cfg, word, "mint")
    return f


# =========================================================================================
# Interrupted: Arthur barges back in mid-cycle. Wide eyes, a big HEY!
# =========================================================================================


@layout(S52, "interrupted")
def _interrupted_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "arthur_shock" if blink(now) else "arthur_shock2")
    head = caption(state, cfg, "interrupted", S52, C52_W, large_font(), fallback="HEY!")
    big52(f, cfg, head, "attention")
    small52(f, cfg, "STOPPED", "text")
    return f


@layout(S32, "interrupted")
def _interrupted_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "arthur_shock")
    word = caption(state, cfg, "interrupted_small", S32, T32_W, small_font(), fallback="HEY!")
    text32(f, cfg, word, "attention" if blink(now) else "text")
    return f


# =========================================================================================
# Idle: Arthur, the drawer and litter levels, and when he last went. Nearly still.
# =========================================================================================


def idle_pose(now: datetime, cfg: Config) -> str | None:
    """``"blink"`` or ``"tail"`` for one second every ``idle_blink_every_s`` (taking turns),
    else None. This is the only thing that animates while idle."""
    every = int(cfg.timings.idle_blink_every_s)
    if every <= 0:
        return None
    k = tick(now)
    if k % every != every - 1:
        return None
    return "blink" if (k // every) % 2 == 0 else "tail"


def _last_visit(state: DisplayState, now: datetime) -> str:
    if state.last_visit_at is None:
        return "NO VISIT"
    return f"{fmt_age(seconds_between(state.last_visit_at, now))} AGO"


def _levels_52(f: Frame, state: DisplayState, cfg: Config, y_bin: int, y_lit: int) -> None:
    """Two labelled gauges, each 3 rows tall: a tiny poop and the drawer level, a tiny sand
    pile and the litter level."""
    draw_art(f, BIN_MARK, C52_X, y_bin, cfg)
    gauge(f, C52_X + 7, y_bin, C52_W - 7, 3, drawer_frac(state), drawer_role(state, cfg), cfg)
    draw_art(f, LITTER_MARK, C52_X, y_lit, cfg)
    gauge(f, C52_X + 7, y_lit, C52_W - 7, 3, litter_frac(state), litter_role(state), cfg)


@layout(S52, "ready", "arthur")
def _ready_arthur_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    pose = idle_pose(now, cfg)
    icon52(f, cfg, {"blink": "arthur_blink", "tail": "arthur_tail"}.get(pose or "", "arthur_sit"))
    _levels_52(f, state, cfg, 1, 5)
    small52(f, cfg, _last_visit(state, now), "text")
    return f


@layout(S52, "ready", "vacant")
def _ready_vacant_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "door", "ok")
    small52(f, cfg, "VACANT", "ok", y=L1_Y)
    _levels_52(f, state, cfg, 8, 12)
    return f


@layout(S52, "ready", "numbers")
def _ready_numbers_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "arthur_blink" if idle_pose(now, cfg) == "blink" else "arthur_sit")
    draw_art(f, BIN_MARK, C52_X, 3, cfg)
    big52(f, cfg, fmt_pct(state.drawer_pct), drawer_role(state, cfg), x=C52_X + 7)
    draw_art(f, LITTER_MARK, C52_X, 11, cfg)
    small52(f, cfg, fmt_pct(state.litter_pct), litter_role(state), x=C52_X + 7)
    return f


@layout(S32, "ready", "face")
def _ready_face_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    icon32(f, cfg, "arthur_face_blink" if idle_pose(now, cfg) == "blink" else "arthur_face")
    vgauge(f, T32_X, 1, 2, 6, drawer_frac(state), drawer_role(state, cfg), cfg)
    vgauge(f, T32_X + 3, 1, 2, 6, litter_frac(state), litter_role(state), cfg)
    age = fmt_age(seconds_between(state.last_visit_at, now)) if state.last_visit_at else "--"
    text32(f, cfg, age, "text", x=T32_X + 7)
    return f


@layout(S32, "ready", "numbers")
def _ready_numbers_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()

    def two(v: float | None) -> str:
        return "--" if v is None else str(min(99, round(max(0.0, v))))

    sm = small_font()
    draw_art(f, BIN_MARK3, 1, 2, cfg)
    put_text(f, 5, T32_Y, two(state.drawer_pct), cfg.rgb(drawer_role(state, cfg)), sm, right=13)
    draw_art(f, LITTER_MARK3, 16, 2, cfg)
    put_text(f, 20, T32_Y, two(state.litter_pct), cfg.rgb(litter_role(state)), sm, right=T32_R)
    return f


# =========================================================================================
# Badges: fixed slots so position as well as colour says which is which.
# =========================================================================================

#: badge → (slot index from the top, role)
BADGE_STYLE: dict[str, tuple[int, str]] = {
    "drawer_nearly_full": (0, "attention"),
    "litter_low": (1, "sand"),
    "laser_dirty": (2, "text"),
    "on_battery": (3, "alert"),
    "polling_only": (4, "ghost"),
}


def draw_badges(f: Frame, badges: frozenset[str], cfg: Config) -> None:
    for badge in badges:
        slot, role = BADGE_STYLE[badge]
        c = cfg.rgb(role)
        if f.size == S32:
            f.pixel(30, 1 + slot, c)
        else:
            f.rect(49, 1 + 3 * slot, 2, 2, c)


# =========================================================================================
# Public API
# =========================================================================================


def _norm_size(size: Size | str | None, cfg: Config) -> Size:
    if size is None:
        return cfg.size
    if isinstance(size, str):
        return parse_size(size)
    s = (int(size[0]), int(size[1]))
    if s not in (S32, S52):
        raise ValueError(f"unsupported size {s}")
    return s


def list_kinds() -> tuple[str, ...]:
    """Every display kind, in layer order."""
    return KINDS


def list_sizes() -> tuple[Size, ...]:
    return (S32, S52)


def list_variants(kind: str, size: Size | str) -> list[str]:
    """Variant names for ``kind`` at ``size``; the first is the default."""
    s = parse_size(size) if isinstance(size, str) else tuple(size)
    try:
        return list(_REGISTRY[(s, kind)])  # type: ignore[index]
    except KeyError:
        raise KeyError(f"no layout for {kind!r} at {s}") from None


def _draw(fn: Layout, state: DisplayState, now: datetime, cfg: Config) -> Frame:
    frame = fn(state, now, cfg)
    if state.kind != "no_data":
        draw_badges(frame, state.badges, cfg)
    return frame


def render(
    state: DisplayState,
    now: datetime,
    cfg: Config,
    size: Size | str | None = None,
    variant: str | None = None,
) -> Frame:
    """Draw ``state`` at ``now``. ``size`` defaults to the config's; ``variant`` to the
    kind's default. A variant name the kind does not have falls back to the default, so a
    caller can pick one variant name for every state.

    Inside :func:`arthur.render.text.strict_text` (the tests), a label that does not fit, or
    anything drawn on or against a label, raises :class:`~arthur.render.text.TextFitError`.
    """
    s = _norm_size(size, cfg)
    variants = _REGISTRY.get((s, state.kind))
    if not variants:
        raise KeyError(f"no layout for {state.kind!r} at {s}")
    fn = variants.get(variant) if variant else None
    if fn is None:
        fn = next(iter(variants.values()))
    if not is_strict():
        return _draw(fn, state, now, cfg)
    with strict_text() as records:
        frame = _draw(fn, state, now, cfg)
        check_text(frame, records)
    return frame
