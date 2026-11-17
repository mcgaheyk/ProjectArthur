"""Layouts: DisplayState + now + config → Frame, at 52x16 (the Ulanzi TC002).

A pure function of its inputs; knows nothing about AWTRIX. Every display kind has at least one
layout; some have named variants. The first variant registered for a kind is the default.

Arthur (an orange tabby) is the mascot; see design-screen section 0 for the direction.

Canvas rules (docs/states.md, design-screen skill):

- The clock's stale border covers row 0, row 15, column 0 and column 51, so nothing is drawn
  there. One deliberate exception: a layout registered with ``edge_ring=True`` (listed in
  :data:`EDGE_RING`; today ``drawer_full`` and ``cleaning``) may draw a 1-pixel ring on
  exactly those pixels. The stale overlay paints over the whole ring, so a stale frame still
  reads as stale, and such a layout keeps its other content one pixel clear of the ring
  (rows 2-13, columns 2-49).
- Every label goes through :func:`arthur.render.text.put_text` with the box it must fit, and
  nothing else may touch a label (the strict text check in the tests).
- A 16x16 picture at x=0, content in x=17..47 (a big-font or small-font first line from row 1,
  a small-font second line on rows 9-13 or the drawer gauge on rows 13-14). Columns 48-50 are
  not reserved: most layouts leave them empty, but the idle Arthur layout shifts its bars and
  bottom line to columns 20-50 (to make room for his long tail in columns 16-18),
  ``drawer_full`` uses columns up to 49 inside its ring, and the ``interrupted`` variants
  other than the default lay out their own columns up to 49 or 50.
- Level colours: the drawer bar is ``drawer`` (orange) below ``drawer_warn_pct`` and
  ``alert`` from there or when full; the litter bar follows the robot's own litter word
  (OPTIMAL ``ok``, REFILL/OVERFILL ``caution``, LOW/EMPTY ``alert``, else ``sand``).
- About one frame a second: animation phases are derived from ``now``. Live states change at
  most once a second. Idle is "alive, not busy": short gestures between still pauses, played
  from a 60-second script (:data:`IDLE_SCRIPT`), and still for most of every minute.
- Captions come from ``[captions]`` in the config: one per visit, picked from when the state
  began (in use's jokes instead take turns, one per joke page, in an order shuffled per visit);
  a caption that does not fit its line is skipped, never cut.
"""

from __future__ import annotations

import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from arthur.config import Config, parse_size
from arthur.model import KINDS, DisplayState
from arthur.render.font import Font, condensed_small_font, large_font, small_font
from arthur.render.frame import Frame
from arthur.render.icons import draw_art, draw_icon, icon_cells
from arthur.render.text import check_text, first_fitting, fits, is_strict, put_text, strict_text

Size = tuple[int, int]
Layout = Callable[[DisplayState, datetime, Config], Frame]

#: The one size: the Ulanzi TC002's 52x16 matrix.
S52: Size = (52, 16)
#: Width and height of the picture on the left (icons, hourglass, drum).
PIC = 16

# Geometry
C52_X = 17  # content starts here (after the 16x16 picture and a 1-px gap)
C52_R = 47  # last content column of most layouts (51 is the border)
C52_W = C52_R - C52_X + 1
L1_Y = 1  # first line: big font rows 1-7, or small font rows 1-5
L2_Y = 9  # second line: small font rows 9-13
GAUGE52_Y = 13  # the drawer gauge live screens share: rows 13-14

_REGISTRY: dict[tuple[Size, str], dict[str, Layout]] = {}

#: (size, kind, variant) of every layout allowed to draw on the outermost rows and columns,
#: where the clock's stale border goes: see the module docstring. The border test consults it.
EDGE_RING: set[tuple[Size, str, str]] = set()


def layout(
    size: Size, kind: str, variant: str = "default", *, edge_ring: bool = False
) -> Callable[[Layout], Layout]:
    """Register a layout function for (size, kind) under ``variant``. ``edge_ring=True``
    registers it as allowed to draw a ring on the outermost pixels (:data:`EDGE_RING`)."""

    def deco(fn: Layout) -> Layout:
        _REGISTRY.setdefault((size, kind), {})[variant] = fn
        if edge_ring:
            EDGE_RING.add((size, kind, variant))
        return fn

    return deco


def has_edge_ring(kind: str, size: Size, variant: str | None = None) -> bool:
    """Whether the layout ``render`` would pick for these draws on the outermost pixels."""
    variants = _REGISTRY.get((size, kind), {})
    if not variants:
        return False
    v = variant if variant in variants else next(iter(variants))
    return (size, kind, v) in EDGE_RING


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
    """Orange while the drawer has room; red from ``drawer_warn_pct`` and when it is full."""
    pct = state.drawer_pct or 0
    if state.kind == "drawer_full" or pct >= cfg.thresholds.drawer_warn_pct:
        return "alert"
    return "drawer"


#: The robot's own litter word (Whisker's ``litterLevelState``) → the litter bar's role.
LITTER_ROLES: dict[str, str] = {
    "OPTIMAL": "ok",
    "REFILL": "caution",
    "OVERFILL": "caution",
    "LOW": "alert",
    "EMPTY": "alert",
}


def litter_role(state: DisplayState) -> str:
    """Follows the robot's litter word, as the phone app does: green when OPTIMAL, yellow to
    top up or when overfilled, red when LOW or EMPTY, neutral sand when there is no word."""
    return LITTER_ROLES.get((state.litter_state or "").upper(), "sand")


def gauge(f: Frame, x: int, y: int, w: int, h: int, frac: float, role: str, cfg: Config) -> None:
    """A horizontal bar: ``frac`` of it lit in ``role`` over a dim track."""
    frac = max(0.0, min(1.0, frac))
    f.rect(x, y, w, h, cfg.rgb("dim"))
    lit = round(frac * w)
    if frac > 0 and lit == 0:
        lit = 1
    f.rect(x, y, lit, h, cfg.rgb(role))


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


def _f52() -> Frame:
    return Frame(*S52)


def icon52(f: Frame, cfg: Config, name: str, accent: str = "text") -> None:
    draw_icon(f, name, 0, 0, cfg, size=PIC, accent=accent)


def big52(f: Frame, cfg: Config, s: str, role: str, y: int = L1_Y, x: int = C52_X) -> None:
    """A line in the big font (7 rows from ``y``)."""
    put_text(f, x, y, s, cfg.rgb(role), large_font(), right=C52_R)


def small52(
    f: Frame, cfg: Config, s: str, role: str, y: int = L2_Y, x: int = C52_X, *,
    right: int = C52_R, condensed: bool = False,
) -> None:
    """A line in the small font (5 rows from ``y``); ``condensed`` uses 3-px M and W."""
    font = condensed_small_font() if condensed else small_font()
    put_text(f, x, y, s, cfg.rgb(role), font, right=right)


def digits52(f: Frame, cfg: Config, s: str, role: str) -> None:
    """Double-size small-font digits on rows 1-10."""
    put_text(f, C52_X, 1, s, cfg.rgb(role), small_font(), right=C52_R, scale=2)


def drawer_gauge52(f: Frame, state: DisplayState, cfg: Config) -> None:
    """The drawer gauge live screens share at 52x16: rows 13-14, full content width."""
    gauge(f, C52_X, GAUGE52_Y, C52_W, 2, drawer_frac(state), drawer_role(state, cfg), cfg)


# Tiny marks that label the idle gauges.
BIN_MARK = ("..B..", ".BHB.", "BBBBB")  # a little poop: the waste drawer
LITTER_MARK = ("s.s.s", ".s.s.", "sssss")  # a little sand pile: the litter
#: A 3x3 "z" for the sleeping moon.
Z_ART = ("CCC", ".C.", "CCC")


# =========================================================================================
# Rare states: one template (icon, headline, a line saying what is going on)
# =========================================================================================


@dataclass(frozen=True)
class Rare:
    icon: str
    accent: str  # the icon's accent role
    headline: str  # the big line
    role: str  # headline colour
    line2: str  # the small line, plain words
    blink_icon: bool = False


#: No-data reason → second-line options (the first that fits is used).
_NO_DATA_REASON: dict[str, tuple[str, ...]] = {
    "auth": ("LOGIN",),
    "api": ("API ERROR", "API ERR"),
    "no_robots": ("NO ROBOT",),
    "timeout": ("TIMEOUT",),
    "no_snapshot": ("WAITING",),
}

#: Fault code → second-line options (the first that fits is used).
FAULT_WORDS: dict[str, tuple[str, ...]] = {
    "PD": ("PINCH",),
    "OTF": ("JAMMED",),
    "CSF": ("CAT SENSOR", "SENSOR"),
}


def rare_for(state: DisplayState, now: datetime) -> Rare:
    sm = small_font()
    kind = state.kind
    if kind == "no_data":
        line2s = _NO_DATA_REASON.get(state.detail or "", ("WHISKER",))
        return Rare("ghost_q", "text", "NO DATA", "alert", first_fitting(line2s, C52_W, sm))
    if kind == "robot_offline":
        age = fmt_age(seconds_between(state.last_seen, now))
        return Rare("wifi_x", "alert", "OFFLINE", "alert",
                    first_fitting([f"SEEN {age}", f"{age} AGO", age], C52_W, sm))
    if kind == "robot_off":
        return Rare("power", "attention", "ROBOT", "attention", "IS OFF")
    if kind == "fault":
        code = state.fault_code or "?"
        line2s = FAULT_WORDS.get(code, (f"CODE {code}", code))
        return Rare("warning", "alert", "FAULT", "alert", first_fitting(line2s, C52_W, sm),
                    blink_icon=True)
    if kind == "bonnet_removed":
        return Rare("bonnet", "attention", "BONNET", "attention", "IS OFF")
    if kind == "paused":
        t = fmt_clock(seconds_between(state.since, now))
        return Rare("pause", "attention", "PAUSED", "attention", t)
    if kind == "emptying":
        return Rare("empty", "gold", "EMPTY", "gold", "CYCLE")
    if kind == "powering":
        if state.detail == "down":
            return Rare("power", "gold", "POWER", "gold", "BYE BYE", blink_icon=True)
        return Rare("power", "gold", "POWER", "gold", "STARTING", blink_icon=True)
    raise KeyError(kind)


def _rare_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    r = rare_for(state, now)
    f = _f52()
    if not r.blink_icon or blink(now):
        icon52(f, cfg, r.icon, r.accent)
    big52(f, cfg, r.headline, r.role)
    small52(f, cfg, r.line2, "text")
    return f


for _kind in ("no_data", "robot_offline", "robot_off", "fault", "bonnet_removed", "paused",
              "emptying", "powering"):
    layout(S52, _kind)(_rare_52)


# =========================================================================================
# Drawer full: the poop emoji, bold FULL, EMPTY NOW, and a red ring flashing
# =========================================================================================

#: Content inside the ring keeps a 1-pixel gap from it: rows 2-13, columns 2-49.
RING_X = 16  # text starts here, right of the emoji (columns 2-14) and a 1-px gap
RING_R = 49  # last content column inside the ring
#: FULL in 6-row bold lettering (the big font's 7 rows do not fit inside the ring), rows 2-7.
FULL_ART = (
    "RRRRRR..RR...RR..RR......RR....",
    "RRRRRR..RR...RR..RR......RR....",
    "RR......RR...RR..RR......RR....",
    "RRRRR...RR...RR..RR......RR....",
    "RR......RRRRRRR..RRRRRR..RRRRRR",
    "RR.......RRRRR...RRRRRR..RRRRRR",
)


#: The poop's face, one entry per second (a 15 s loop on wall-clock seconds), shared by
#: drawer-full and in-use's joke page. Mostly he sits smiling; now and then he blinks, glances
#: left and right, and winks once. The loop runs on its own, not with drawer-full's ring (its
#: length is odd and the ring's period is 2 s, so each time round every face lands on the other
#: ring phase) nor with in-use's pages. Only the eyes move; the smile stays.
POOP_FACE_LOOP: tuple[str, ...] = (
    "poop_full", "poop_full", "poop_full_blink", "poop_full", "poop_full",
    "poop_full_left", "poop_full_right", "poop_full", "poop_full", "poop_full",
    "poop_full_blink", "poop_full", "poop_full_wink", "poop_full", "poop_full",
)


def edge_ring(f: Frame, cfg: Config, role: str) -> None:
    """A 1-pixel ring on the outermost pixels: only for layouts registered ``edge_ring``."""
    f.rect(0, 0, f.width, f.height, cfg.rgb(role), filled=False)


@layout(S52, "drawer_full", edge_ring=True)
def _drawer_full_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    if blink(now):
        edge_ring(f, cfg, "alert")
    icon52(f, cfg, POOP_FACE_LOOP[phase(now, len(POOP_FACE_LOOP))])
    draw_art(f, FULL_ART, RING_X, 2, cfg)
    width = RING_R - RING_X + 1
    sub = caption(state, cfg, "drawer_full", S52, width, condensed_small_font(),
                  fallback="EMPTY IT")
    small52(f, cfg, sub, "text", y=9, x=RING_X, right=RING_R, condensed=True)
    return f


# =========================================================================================
# In use: the funny moment. Two pages take turns: the poop emoji with a joke, then Arthur
# with the timer.
# =========================================================================================

#: Cat-page poses, one per second (a 9 s loop of its own, on wall-clock seconds): Arthur sits
#: half-lidded and smirking ("do you mind?"), glances to each side, raises a brow, blinks
#: slowly and flicks his tail. The smirk never moves, so every frame carries the joke.
SMUG_LOOP: tuple[str, ...] = (
    "arthur_smug", "arthur_smug", "arthur_smug_left", "arthur_smug_right", "arthur_smug",
    "arthur_smug_brow", "arthur_smug", "arthur_smug_blink", "arthur_smug_tail",
)
#: The poop emoji drawn one row lower than on drawer-full (rows 3-14), so the stink lines have
#: rows 1-5 beside its tip.
POOP_Y = 1
#: A stink wisp: a little S, 2 columns by 4 rows.
WISP = (".K", "K.", "K.", ".K")
#: Rows the wisps may use: beside the poop's tip, a pixel clear of it, off the border row.
STINK_TOP, STINK_BOTTOM = 1, 5
#: The wisps are a stream: one every ``STINK_CYCLE`` rows (the wisp and a 2-row gap), the whole
#: stream climbing a row a second, so every lit pixel moves straight up. A 6 s cycle.
STINK_CYCLE = 6
#: (left column, phase offset) of each side's wisp: left at columns 2-3, right at 13-14, half a
#: cycle apart so one is always low while the other is high.
STINK_SIDES = ((2, 0), (13, 3))
def _in_use_timer(state: DisplayState, now: datetime) -> str:
    return fmt_clock(seconds_between(state.since, now))


def in_use_page(state: DisplayState, now: datetime, cfg: Config) -> tuple[str, int]:
    """``("joke" | "cat", loop number)``. The loop is the joke page for ``in_use_joke_s``
    then the cat page for ``in_use_cat_s``, counted from when Arthur stepped in, so every visit
    opens on a joke."""
    joke_s = max(1, int(cfg.timings.in_use_joke_s))
    cat_s = max(0, int(cfg.timings.in_use_cat_s))
    began = state.since or state.last_seen
    t = tick(now) - (math.floor(began.timestamp()) if began is not None else 0)
    k, into = divmod(t, joke_s + cat_s)
    return ("joke" if into < joke_s else "cat"), k


def turn_caption(
    state: DisplayState,
    cfg: Config,
    key: str,
    turn: int,
    size: Size,
    width: int,
    font: Font,
    *,
    fallback: str,
) -> str:
    """The ``[captions] key`` joke for the ``turn``-th time its page comes round.

    The captions that fit are shuffled once per visit (seeded from when the state began, like
    :func:`caption`) and shown in that order, one per turn, so every one shows before any
    repeats and two visits start in different places.
    """
    CAPTION_USES.setdefault(key, set()).add((size, width, font, 1))
    options = [c for c in cfg.captions.get(key, ()) if fits(c, width, font)]
    if not options:
        return fallback
    began = state.since or state.last_seen
    seed = zlib.crc32(f"{key}|{math.floor(began.timestamp()) if began else 0}".encode())
    random.Random(seed).shuffle(options)
    return options[turn % len(options)]


def stink(f: Frame, cfg: Config, now: datetime) -> None:
    """Two streams of stink wisps rising beside the poop's tip, a row a second."""
    color = cfg.rgb("stink")
    n = len(WISP)
    for x0, offset in STINK_SIDES:
        for y in range(STINK_TOP, STINK_BOTTOM + 1):
            i = (y + tick(now) + offset) % STINK_CYCLE  # row of the wisp (or gap) shown here
            if i < n:
                for dx, ch in enumerate(WISP[i]):
                    if ch != ".":
                        f.pixel(x0 + dx, y, color)


@layout(S52, "in_use")
def _in_use_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    page, k = in_use_page(state, now, cfg)
    if page == "cat":
        icon52(f, cfg, SMUG_LOOP[phase(now, len(SMUG_LOOP))])
        digits52(f, cfg, _in_use_timer(state, now), "text")
        drawer_gauge52(f, state, cfg)
        return f
    draw_icon(f, POOP_FACE_LOOP[phase(now, len(POOP_FACE_LOOP))], 0, POOP_Y, cfg, size=PIC)
    stink(f, cfg, now)
    joke = turn_caption(state, cfg, "in_use", k, S52, C52_W, large_font(), fallback="BUSY")
    big52(f, cfg, joke, "fur")
    small52(f, cfg, "IN USE", "text")
    return f


# =========================================================================================
# Waiting: cream hourglass, orange sand, gold digits. Sleeping: a moon and z's.
# =========================================================================================

#: Row of the hourglass neck.
_NECK = 7


def hourglass(f: Frame, cfg: Config, left: float, now: datetime) -> None:
    """The hourglass with ``left`` (0..1) of the sand still in the top bulb; a grain falls
    through the neck every other second."""
    draw_icon(f, "hourglass", 0, 0, cfg, size=PIC)
    neck = _NECK
    cells = icon_cells("hourglass", PIC, "%")
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


@layout(S52, "waiting")
def _waiting_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    if state.sleeping:
        return _sleep_52(state, now, cfg)
    f = _f52()
    hourglass(f, cfg, wait_fraction_left(state, now), now)
    cap = caption(state, cfg, "waiting", S52, C52_W, small_font(), fallback="CLEAN IN")
    small52(f, cfg, cap, "sand", y=L1_Y)
    big52(f, cfg, _wait_text(state, now), "gold", y=7)
    return f


# =========================================================================================
# Cleaning: soapy blue. The globe turning inside a ring with bubbles running round it.
# =========================================================================================

#: The drum's top-left inside the ring's 1-pixel gap: it covers columns 2-13, rows 2-13.
DRUM_X, DRUM_Y = 2, 2
#: Bubbles on the cleaning ring: a ``BUBBLE_LEN``-pixel run of ``soap_hi`` every
#: ``BUBBLE_EVERY`` pixels round the ring, moving ``BUBBLE_STEP`` pixels clockwise a second.
BUBBLE_EVERY = 11  # 132 ring pixels / 11 = 12 bubbles
BUBBLE_LEN = 2
BUBBLE_STEP = 1

#: The countdown in 6-row bold digits (the big font's 7 rows leave no gap inside the ring), in
#: the style of drawer-full's FULL: 2-pixel uprights, 1-pixel bars. Drawn in ``text``; "M", "H"
#: and "D" cover the rare long timers (``fmt_clock``).
BOLD_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": (".WWWW.", "WW..WW", "WW..WW", "WW..WW", "WW..WW", ".WWWW."),
    "1": ("..WW..", ".WWW..", "..WW..", "..WW..", "..WW..", ".WWWW."),
    "2": ("WWWWW.", "....WW", ".WWWW.", "WW....", "WW....", "WWWWWW"),
    "3": ("WWWWW.", "....WW", "..WWW.", "....WW", "....WW", "WWWWW."),
    "4": ("WW..WW", "WW..WW", "WWWWWW", "....WW", "....WW", "....WW"),
    "5": ("WWWWWW", "WW....", "WWWWW.", "....WW", "....WW", "WWWWW."),
    "6": (".WWWW.", "WW....", "WWWWW.", "WW..WW", "WW..WW", ".WWWW."),
    "7": ("WWWWWW", "....WW", "...WW.", "..WW..", "..WW..", "..WW.."),
    "8": (".WWWW.", "WW..WW", ".WWWW.", "WW..WW", "WW..WW", ".WWWW."),
    "9": (".WWWW.", "WW..WW", "WW..WW", ".WWWWW", "....WW", ".WWWW."),
    ":": ("..", "WW", "..", "..", "WW", ".."),
    "-": ("....", "....", "WWWW", "WWWW", "....", "...."),
    "M": ("WW...WW", "WWW.WWW", "WWWWWWW", "WW.W.WW", "WW...WW", "WW...WW"),
    "H": ("WW..WW", "WW..WW", "WWWWWW", "WW..WW", "WW..WW", "WW..WW"),
    "D": ("WWWWW.", "WW..WW", "WW..WW", "WW..WW", "WW..WW", "WWWWW."),
}
#: Space after each bold glyph: 2 pixels, 1 either side of a colon.
BOLD_GAP = 2


def bold_width(s: str) -> int:
    """Width of ``s`` in :data:`BOLD_GLYPHS`."""
    w = 0
    for i, ch in enumerate(s):
        w += len(BOLD_GLYPHS[ch][0])
        if i < len(s) - 1:
            w += 1 if ":" in (ch, s[i + 1]) else BOLD_GAP
    return w


def bold_text(f: Frame, cfg: Config, s: str, x: int, y: int, role: str) -> int:
    """Draw ``s`` in the bold digits with its top-left at (x, y); returns the width."""
    for i, ch in enumerate(s):
        art = BOLD_GLYPHS[ch]
        draw_art(f, art, x, y, cfg, override=role)
        x += len(art[0])
        if i < len(s) - 1:
            x += 1 if ":" in (ch, s[i + 1]) else BOLD_GAP
    return bold_width(s)


def ring_pixels(w: int, h: int) -> list[tuple[int, int]]:
    """The outermost pixels, clockwise from the top-left corner."""
    top = [(x, 0) for x in range(w)]
    right = [(w - 1, y) for y in range(1, h)]
    bottom = [(x, h - 1) for x in range(w - 2, -1, -1)]
    left = [(0, y) for y in range(h - 2, 0, -1)]
    return top + right + bottom + left


def soap_ring(f: Frame, cfg: Config, now: datetime) -> None:
    """The cleaning ring: ``soap`` all round, with ``soap_hi`` bubbles running clockwise."""
    edge_ring(f, cfg, "soap")
    hi = cfg.rgb("soap_hi")
    shift = tick(now) * BUBBLE_STEP
    for i, (x, y) in enumerate(ring_pixels(f.width, f.height)):
        if (i - shift) % BUBBLE_EVERY < BUBBLE_LEN:
            f.pixel(x, y, hi)


#: The globe, hand-drawn to fit the 12x12 box inside the ring: ``b`` the shell (its opening is
#: cut out by angle as it turns), ``s`` the litter lying in the bottom.
DRUM_ART = (
    "....bbbb....",
    "..bb....bb..",
    ".b........b.",
    ".b........b.",
    "b..........b",
    "b..........b",
    "b..........b",
    "b.ssssssss.b",
    ".bssssssssb.",
    ".b.ssssss.b.",
    "..bb.ss.bb..",
    "....bbbb....",
)


#: The drum's opening turns an eighth of a turn a second.
DRUM_STEPS = 8


def drum_angle(now: datetime) -> float:
    """Where the drum's opening points at ``now``, in radians on screen axes (y down):
    straight up at step 0, then clockwise."""
    return phase(now, DRUM_STEPS) * 2 * math.pi / DRUM_STEPS - math.pi / 2


def drum(
    f: Frame, cfg: Config, now: datetime, x0: int = DRUM_X, y0: int = DRUM_Y,
    role: str = "soap",
) -> None:
    """The robot's globe at (x0, y0): a shell in ``role`` (soapy blue on cleaning) whose
    opening turns a step (an eighth of a turn) every second, with litter lying in the
    bottom."""
    c = (len(DRUM_ART) - 1) / 2
    theta = drum_angle(now)
    gap = math.radians(40)
    soap, sand = cfg.rgb(role), cfg.rgb("sand")
    for y, row in enumerate(DRUM_ART):
        for x, ch in enumerate(row):
            if ch == "s":
                f.pixel(x0 + x, y0 + y, sand)
            elif ch == "b":
                diff = (math.atan2(y - c, x - c) - theta + math.pi) % (2 * math.pi) - math.pi
                if abs(diff) > gap:
                    f.pixel(x0 + x, y0 + y, soap)


def _clean_left(state: DisplayState, now: datetime) -> str:
    if state.cycle_end is None:
        return "--"
    return fmt_countdown((state.cycle_end - now).total_seconds())


@layout(S52, "cleaning", edge_ring=True)
def _cleaning_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    soap_ring(f, cfg, now)
    drum(f, cfg, now)
    bold_text(f, cfg, _clean_left(state, now), RING_X, 2, "text")
    width = RING_R - RING_X + 1
    word = caption(state, cfg, "cleaning", S52, width, small_font(), fallback="CLEANING")
    small52(f, cfg, word, "soap", y=9, x=RING_X, right=RING_R)
    return f


# =========================================================================================
# Just cleaned: a twinkling soapy-blue sparkle and FRESHLY CLEANED, for a few seconds.
# =========================================================================================


@layout(S52, "just_cleaned")
def _just_cleaned_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon52(f, cfg, "sparkle_a" if blink(now) else "sparkle_b")
    small52(f, cfg, "FRESHLY", "soap_hi", y=L1_Y)
    head = caption(state, cfg, "just_cleaned", S52, C52_W, large_font(), fallback="CLEANED")
    big52(f, cfg, head, "soap", y=7)
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


# =========================================================================================
# Interrupted, variants to compare: Arthur comes to have a look at the globe turning. Little
# walk-in scenes (stroll) and big-eyed close-ups (peek).
# =========================================================================================


#: Stroll's caption: small font across the top, left of the globe.
STROLL_X, STROLL_R = 2, 36
#: The globe's top-left: columns 38-49, rows 2-13, its opening on the left.
GLOBE_X, GLOBE_Y = 38, 2
GLOBE_EMPTY = (
    "....CCCC....",
    "..CC....CC..",
    ".C........C.",
    ".C........C.",
    "...........C",
    "...........C",
    "...........C",
    "..ssssssss.C",
    ".CssssssssC.",
    ".C.ssssss.C.",
    "..CC.ss.CC..",
    "....CCCC....",
)
#: The globe with Arthur inside, looking out of the opening.
GLOBE_CAT = (
    "....CCCC....",
    "..CC....CC..",
    ".C........C.",
    ".CO.....O.C.",
    "..OO...OO..C",
    "..OOOOOOO..C",
    "..EEOOOEE..C",
    "..OOCNCOO..C",
    ".CssssssssC.",
    ".C.ssssss.C.",
    "..CC.ss.CC..",
    "....CCCC....",
)
#: The same, blinking.
GLOBE_CAT_BLINK = (*GLOBE_CAT[:6], "..SSOOOSS..C", *GLOBE_CAT[7:])
#: Arthur walking, side view, facing right: 12 wide, 7 tall, feet on the bottom row.
WALK_A = (
    "S.......O.O.",
    "O.......OOO.",
    "O..OSOSOOEO.",
    ".OOOOOOOOON.",
    ".OOOOOOOOO..",
    ".OO.....OO..",
    ".O.O...O.O..",
)
#: The other stride.
WALK_B = (*WALK_A[:5], "..OO...OO...", "..O....O....")
#: Top row of the walking cat: his feet land on row 13.
WALK_Y = 7
#: His tail hanging out of the opening once he is inside, flicking: two poses.
STROLL_TAIL = (("...", ".OO", "S.."), ("S..", ".OO", "..."))
STROLL_TAIL_X, STROLL_TAIL_Y = 35, 8
#: The scene, one step a second (a 9 s loop from when the state began): (cat art and x, or
#: None; globe art). Four strides in, the head through the opening, then he looks out.
STROLL_STEPS: tuple[tuple[tuple[tuple[str, ...], int] | None, tuple[str, ...]], ...] = (
    ((WALK_A, 2), GLOBE_EMPTY),
    ((WALK_B, 9), GLOBE_EMPTY),
    ((WALK_A, 16), GLOBE_EMPTY),
    ((WALK_B, 23), GLOBE_EMPTY),
    ((WALK_A, 30), GLOBE_EMPTY),  # the globe is drawn over him: his head is inside
    (None, GLOBE_CAT),
    (None, GLOBE_CAT),
    (None, GLOBE_CAT_BLINK),
    (None, GLOBE_CAT),
)


def loop_step(state: DisplayState, now: datetime, n: int) -> tuple[int, int]:
    """``(loop number, step)`` of an ``n``-second loop counted from when the state began, so
    every interruption opens on step 0 of loop 0."""
    t = tick(now) - (math.floor(state.since.timestamp()) if state.since is not None else 0)
    return divmod(t, n)


def stroll_step(state: DisplayState, now: datetime) -> int:
    """Which step of :data:`STROLL_STEPS` shows: counted from when the state began, so
    every interruption opens with him walking in."""
    return loop_step(state, now, len(STROLL_STEPS))[1]


@layout(S52, "interrupted", "stroll")
def _interrupted_stroll_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    width = STROLL_R - STROLL_X + 1
    cap = caption(state, cfg, "interrupted_stroll", S52, width, small_font(),
                  fallback="HE'S BACK")
    small52(f, cfg, cap, "attention", y=L1_Y, x=STROLL_X, right=STROLL_R)
    step = stroll_step(state, now)
    cat, globe = STROLL_STEPS[step]
    if cat is not None:
        art, x = cat
        draw_art(f, art, x, WALK_Y, cfg)
    draw_art(f, globe, GLOBE_X, GLOBE_Y, cfg)
    if cat is None:
        draw_art(f, STROLL_TAIL[(step - 5) % 2], STROLL_TAIL_X, STROLL_TAIL_Y, cfg)
    return f


#: Peek: Arthur's head, close up, at (1, 1) (columns 1-20, rows 1-14); text from column 23.
PEEK_X, PEEK_Y = 1, 1
PEEK_TEXT_X, PEEK_TEXT_R = 23, 50
PEEK_CENTRE = (
    ".OO..............OO.",
    "OOOO............OOOO",
    "ONNOO..........OONNO",
    "ONNOOOOOOOOOOOOOONNO",
    "OOOOOOSOOSSOOSOOOOOO",
    "OOOOOOSOOSSOOSOOOOOO",
    "OOWWWWWOOOOOOWWWWWOO",
    "OOWEEEWOOOOOOWEEEWOO",
    "OOWE.EWOOOOOOWE.EWOO",
    "OOWEEEWOOOOOOWEEEWOO",
    "OOWWWWWOOCCOOWWWWWOO",
    "SOOOOOOCCNNCCOOOOOOS",
    "OOOOOOCCCNNCCCOOOOOO",
    ".OOOOCCCSCCSCCCOOOO.",
)
PEEK_LEFT = (*PEEK_CENTRE[:8], PEEK_CENTRE[8].replace("WE.EW", "W.EEW"), *PEEK_CENTRE[9:])
PEEK_RIGHT = (*PEEK_CENTRE[:8], PEEK_CENTRE[8].replace("WE.EW", "WEE.W"), *PEEK_CENTRE[9:])
PEEK_BLINK = (
    *PEEK_CENTRE[:6],
    "OOOOOOOOOOOOOOOOOOOO",
    "OOOOOOOOOOOOOOOOOOOO",
    "OOSSSSSOOOOOOSSSSSOO",
    "OOOOOOOOOOOOOOOOOOOO",
    "OOOOOOOOOCCOOOOOOOOO",
    *PEEK_CENTRE[11:],
)
#: Peek's 8 s loop: a look to each side and one blink.
PEEK_LOOP = (PEEK_CENTRE, PEEK_CENTRE, PEEK_LEFT, PEEK_RIGHT, PEEK_CENTRE, PEEK_BLINK,
             PEEK_CENTRE, PEEK_CENTRE)


@layout(S52, "interrupted", "peek")
def _interrupted_peek_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    draw_art(f, PEEK_LOOP[phase(now, len(PEEK_LOOP))], PEEK_X, PEEK_Y, cfg)
    width = PEEK_TEXT_R - PEEK_TEXT_X + 1
    head = caption(state, cfg, "interrupted_cat", S52, width, large_font(), fallback="WHAT?")
    put_text(f, PEEK_TEXT_X, L1_Y, head, cfg.rgb("attention"), large_font(), right=PEEK_TEXT_R)
    small52(f, cfg, "STOPPED", "text", x=PEEK_TEXT_X, right=PEEK_TEXT_R)
    return f


#: The full-width caption line across the top: small font, rows 1-5, columns 2-50.
LOOK_X, LOOK_R = 2, 50
LOOK_W = LOOK_R - LOOK_X + 1


def look_caption(state: DisplayState, cfg: Config, loop: int) -> str:
    """What Arthur is up to, a new phrase each time the loop comes round: small font, the
    full width."""
    return turn_caption(state, cfg, "interrupted_look", loop, S52, LOOK_W, small_font(),
                        fallback="INTERRUPTION")


def look_line(f: Frame, cfg: Config, s: str) -> None:
    small52(f, cfg, s, "attention", y=L1_Y, x=LOOK_X, right=LOOK_R)


#: stroll_look's small globe, 8x8, cream with sand, its opening on the left.
SMALL_GLOBE = (
    "..CCCC..",
    ".C....C.",
    ".......C",
    ".......C",
    ".......C",
    "C.ssss.C",
    ".CssssC.",
    "..CCCC..",
)
SMALL_GLOBE_X, SMALL_GLOBE_Y = 42, 7
#: stroll_look's walking cat stands a row lower than stroll's: feet on row 14.
LOOK_WALK_Y = 8
#: Walk A with the tail lowered (column 0): for the flick while he stands looking in.
WALK_A_LOW = (
    "." + WALK_A[0][1:],
    "S" + WALK_A[1][1:],
    "O" + WALK_A[2][1:],
    *WALK_A[3:],
)
#: stroll_look's 8 s loop: (cat art, x). Four strides, then he stands with his nose in the
#: opening, flicking his tail.
LOOK_STEPS: tuple[tuple[tuple[str, ...], int], ...] = (
    (WALK_A, 2), (WALK_B, 10), (WALK_A, 18), (WALK_B, 26),
    (WALK_A, 32), (WALK_A_LOW, 32), (WALK_A, 32), (WALK_A_LOW, 32),
)


@layout(S52, "interrupted", "stroll_look")
def _interrupted_stroll_look_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    loop, step = loop_step(state, now, len(LOOK_STEPS))
    look_line(f, cfg, look_caption(state, cfg, loop))
    art, x = LOOK_STEPS[step]
    draw_art(f, art, x, LOOK_WALK_Y, cfg)
    draw_art(f, SMALL_GLOBE, SMALL_GLOBE_X, SMALL_GLOBE_Y, cfg)  # over his nose
    return f


#: stroll_watch: the drum, in cream, at (8, 3) (columns 8-19, rows 3-14), on the same floor
#: row as Arthur.
WATCH_DRUM_X, WATCH_DRUM_Y = 8, 3
#: Arthur from behind, sitting beside the drum: 7x8 at (1, 7). The first ``BACK_HEAD_ROWS``
#: rows are his head, which leans a column right, towards the drum, to follow the opening.
BACK = (
    "O....O.",
    "OO..OO.",
    "OOOOOO.",
    "OSOOSO.",
    ".OOOOO.",
    "OOSOSOO",
    "OOOOOOO",
    "OOOOOOO",
)
BACK_HEAD_ROWS = 4
BACK_LEAN = (*("." + r[:-1] for r in BACK[:BACK_HEAD_ROWS]), *BACK[BACK_HEAD_ROWS:])
BACK_X, BACK_Y = 1, 7
#: Text right of the drum (column 20 stays a gap); WATCHING needs the condensed small font.
WATCH_TEXT_X, WATCH_TEXT_R = 21, 50


@layout(S52, "interrupted", "stroll_watch")
def _interrupted_stroll_watch_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    drum(f, cfg, now, WATCH_DRUM_X, WATCH_DRUM_Y, role="cream")
    lean = math.cos(drum_angle(now)) > 1e-6  # the opening is in the drum's right half
    draw_art(f, BACK_LEAN if lean else BACK, BACK_X, BACK_Y, cfg)
    small52(f, cfg, "WATCHING", "text", y=L1_Y, x=WATCH_TEXT_X, right=WATCH_TEXT_R,
            condensed=True)
    put_text(f, WATCH_TEXT_X, 7, "IT SPIN", cfg.rgb("attention"), large_font(),
             right=WATCH_TEXT_R)
    return f


#: stroll_card's title, big font, rows 2-8 (exactly 50 px wide).
CARD_TITLE = "INTERRUPTION"
CARD_X, CARD_R, CARD_Y = 1, 50, 2
#: Dashes under the title on rows 11-12: runs of ``CARD_DASH`` lit then unlit, swapping
#: every second. Rows 9-10 stay empty so nothing touches the title.
CARD_DASH = 3
CARD_DASH_Y = 11
#: stroll_card's 10 s loop: three seconds of title card, then the scene at full height,
#: (cat art and x, or None; globe; tail pose or None).
CARD_STEPS: tuple[
    tuple[tuple[tuple[str, ...], int] | None, tuple[str, ...], int | None] | None, ...
] = (
    None, None, None,
    ((WALK_A, 2), GLOBE_EMPTY, None),
    ((WALK_B, 10), GLOBE_EMPTY, None),
    ((WALK_A, 18), GLOBE_EMPTY, None),
    ((WALK_B, 26), GLOBE_EMPTY, None),
    ((WALK_A, 30), GLOBE_EMPTY, None),  # the globe is drawn over him: his head is inside
    (None, GLOBE_CAT, 0),
    (None, GLOBE_CAT, 1),
)


def dashes(f: Frame, cfg: Config, now: datetime, x0: int, x1: int, y: int, h: int) -> None:
    """A row of dashes from column ``x0`` to ``x1``, ``h`` rows tall from ``y``, in
    ``attention``: lit and unlit runs swapping every second."""
    color = cfg.rgb("attention")
    on = tick(now) % 2
    for x in range(x0, x1 + 1):
        if ((x - x0) // CARD_DASH) % 2 == on:
            for dy in range(h):
                f.pixel(x, y + dy, color)


@layout(S52, "interrupted", "stroll_card")
def _interrupted_stroll_card_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _, step = loop_step(state, now, len(CARD_STEPS))
    scene = CARD_STEPS[step]
    if scene is None:
        put_text(f, CARD_X, CARD_Y, CARD_TITLE, cfg.rgb("attention"), large_font(),
                 right=CARD_R)
        dashes(f, cfg, now, CARD_X, CARD_R, CARD_DASH_Y, 2)
        return f
    cat, globe, tail = scene
    if cat is not None:
        art, x = cat
        draw_art(f, art, x, WALK_Y, cfg)
    draw_art(f, globe, GLOBE_X, GLOBE_Y, cfg)
    if tail is not None:
        draw_art(f, STROLL_TAIL[tail], STROLL_TAIL_X, STROLL_TAIL_Y, cfg)
    return f


#: The top of Arthur's head, 20x8 (ears, forehead, eyes), cut off by the bottom edge.
POP_CENTRE = (
    ".OO..............OO.",
    "OOOO............OOOO",
    "ONNOOOOOOOOOOOOOONNO",
    "OOOOOOSOOSSOOSOOOOOO",
    "OOWWWWWOOOOOOWWWWWOO",
    "OOWEEEWOOOOOOWEEEWOO",
    "OOWE.EWOOOOOOWE.EWOO",
    "OOWEEEWOOOOOOWEEEWOO",
)
POP_LEFT = (*POP_CENTRE[:6], POP_CENTRE[6].replace("WE.EW", "W.EEW"), *POP_CENTRE[7:])
POP_RIGHT = (*POP_CENTRE[:6], POP_CENTRE[6].replace("WE.EW", "WEE.W"), *POP_CENTRE[7:])
#: peek_pop's 8 s loop: (art, rows showing). He rises from the bottom edge, looks round and
#: sinks.
POP_STEPS: tuple[tuple[tuple[str, ...], int], ...] = (
    (POP_CENTRE, 3), (POP_CENTRE, 6), (POP_CENTRE, 8), (POP_LEFT, 8), (POP_RIGHT, 8),
    (POP_CENTRE, 8), (POP_CENTRE, 8), (POP_CENTRE, 3),
)
#: Where he pops up, a new place each loop.
POP_XS = (3, 16, 29)


@layout(S52, "interrupted", "peek_pop")
def _interrupted_peek_pop_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    loop, step = loop_step(state, now, len(POP_STEPS))
    look_line(f, cfg, look_caption(state, cfg, loop))
    art, n = POP_STEPS[step]
    draw_art(f, art[:n], POP_XS[loop % len(POP_XS)], 15 - n, cfg)
    return f


#: peek_spin: an 18x14 head at (1, 1) with blank white eyes; code draws the irises.
SPIN_HEAD = (
    ".OO............OO.",
    "OOOO..........OOOO",
    "ONNOO........OONNO",
    "ONNOOOOOOOOOOOONNO",
    "OOOOOSOOSSOOSOOOOO",
    "OOOOOSOOSSOOSOOOOO",
    "OWWWWWOOOOOOWWWWWO",
    "OWWWWWOOOOOOWWWWWO",
    "OWWWWWOOOOOOWWWWWO",
    "OWWWWWOOOOOOWWWWWO",
    "OWWWWWOOCCOOWWWWWO",
    "SOOOOOCCNNCCOOOOOS",
    "OOOOOCCCNNCCCOOOOO",
    ".OOOCCCSCCSCCCOOO.",
)
SPIN_X, SPIN_Y = 1, 1
#: Top-left of each eye's 5x5 white, in the art.
SPIN_WHITES = ((1, 6), (12, 6))
#: A 3x3 iris with an unlit pupil (drawn in ``off`` over the white).
SPIN_IRIS = ("EEE", "E.E", "EEE")
#: The irises go round clockwise, a step a second, starting at the top.
SPIN_PATH = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
SPIN_TEXT_X, SPIN_TEXT_R = 20, 50


@layout(S52, "interrupted", "peek_spin")
def _interrupted_peek_spin_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    draw_art(f, SPIN_HEAD, SPIN_X, SPIN_Y, cfg)
    dx, dy = SPIN_PATH[phase(now, len(SPIN_PATH))]
    for wx, wy in SPIN_WHITES:
        x, y = SPIN_X + wx + 1 + dx, SPIN_Y + wy + 1 + dy
        draw_art(f, SPIN_IRIS, x, y, cfg)
        f.pixel(x + 1, y + 1, cfg.rgb("off"))
    small52(f, cfg, "WATCHING", "text", y=L1_Y, x=SPIN_TEXT_X, right=SPIN_TEXT_R,
            condensed=True)
    put_text(f, SPIN_TEXT_X, 7, "IT SPIN", cfg.rgb("attention"), large_font(),
             right=SPIN_TEXT_R)
    return f


#: peek_pov: the view from inside the box. His paws hooked over the bottom edge, 7x4, either
#: side of his face (rows 11-14).
POV_PAW = (
    ".CCCCC.",
    "CCSCSCC",
    "CCCCCCC",
    "OOOOOOO",
)
POV_PAWS = ((8, 11), (37, 11))
#: His face, the peek head, between the paws.
POV_FACE_X, POV_FACE_Y = 16, 1
#: Peek's head with the eyes half open.
PEEK_HALF = (
    *PEEK_CENTRE[:6],
    "OOOOOOOOOOOOOOOOOOOO",
    "OOWWWWWOOOOOOWWWWWOO",
    "OOWE.EWOOOOOOWE.EWOO",
    "OOWWWWWOOOOOOWWWWWOO",
    "OOOOOOOOOCCOOOOOOOOO",
    *PEEK_CENTRE[11:],
)
#: The words, big font across the full width, rows 2-8.
POV_X, POV_R, POV_Y = 1, 50, 2
#: peek_pov's 10 s loop: three seconds of words (None), then his face, eyes opening.
POV_STEPS: tuple[tuple[str, ...] | None, ...] = (
    None, None, None,
    PEEK_BLINK, PEEK_HALF, PEEK_CENTRE, PEEK_LEFT, PEEK_RIGHT, PEEK_CENTRE, PEEK_CENTRE,
)


@layout(S52, "interrupted", "peek_pov")
def _interrupted_peek_pov_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    loop, step = loop_step(state, now, len(POV_STEPS))
    for x, y in POV_PAWS:
        draw_art(f, POV_PAW, x, y, cfg)
    face = POV_STEPS[step]
    if face is None:
        words = turn_caption(state, cfg, "interrupted_look", loop, S52, POV_R - POV_X + 1,
                             large_font(), fallback="INTERRUPTION")
        put_text(f, POV_X, POV_Y, words, cfg.rgb("attention"), large_font(), right=POV_R)
    else:
        draw_art(f, face, POV_FACE_X, POV_FACE_Y, cfg)
    return f


#: stroll_pov, stroll_card and peek_pov joined into one story. A beat is (what, art, x):
#: ``"walk"`` the walking cat ``art`` at ``x`` with the empty globe (the last stride drawn
#: under the globe, his head through the opening), ``"paws"`` only the paws, ``"face"`` the
#: paws and the face ``art``, ``"title"`` the paws and CARD_TITLE, ``"words"`` the paws and
#: a phrase taking turns per hold loop.
Beat = tuple[str, tuple[str, ...] | None, int]
#: The opening, played once from when the state began: he walks up to the globe and goes in,
#: then we are inside the box and he looks in at us, eyes opening, and the title.
STORY_INTRO: tuple[Beat, ...] = (
    ("walk", WALK_A, 6), ("walk", WALK_B, 14), ("walk", WALK_A, 22), ("walk", WALK_B, 30),
    ("paws", None, 0),
    ("face", PEEK_BLINK, 0), ("face", PEEK_HALF, 0), ("face", PEEK_CENTRE, 0),
    ("face", PEEK_LEFT, 0), ("face", PEEK_RIGHT, 0),
    ("title", None, 0), ("title", None, 0),
)
#: Then, for as long as the state lasts, an 8 s loop: he looks round and blinks, then a
#: phrase, a new one each time round.
STORY_HOLD: tuple[Beat, ...] = (
    ("face", PEEK_CENTRE, 0), ("face", PEEK_LEFT, 0), ("face", PEEK_CENTRE, 0),
    ("face", PEEK_RIGHT, 0), ("face", PEEK_BLINK, 0),
    ("words", None, 0), ("words", None, 0), ("words", None, 0),
)


def story_beat(state: DisplayState, now: datetime) -> tuple[str, int, int]:
    """``("intro" | "hold", step, hold loop)`` of stroll_pov at ``now``, counted from when the
    state began (the hold loop is 0 during the intro)."""
    t = max(0, tick(now) - (math.floor(state.since.timestamp()) if state.since else 0))
    if t < len(STORY_INTRO):
        return "intro", t, 0
    loop, step = divmod(t - len(STORY_INTRO), len(STORY_HOLD))
    return "hold", step, loop


@layout(S52, "interrupted", "stroll_pov")
def _interrupted_stroll_pov_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    part, step, loop = story_beat(state, now)
    what, art, x = (STORY_INTRO if part == "intro" else STORY_HOLD)[step]
    if what == "walk":
        assert art is not None
        draw_art(f, art, x, WALK_Y, cfg)
        draw_art(f, GLOBE_EMPTY, GLOBE_X, GLOBE_Y, cfg)
        return f
    for px, py in POV_PAWS:
        draw_art(f, POV_PAW, px, py, cfg)
    if what == "face":
        assert art is not None
        draw_art(f, art, POV_FACE_X, POV_FACE_Y, cfg)
    elif what in ("title", "words"):
        words = CARD_TITLE if what == "title" else turn_caption(
            state, cfg, "interrupted_look", loop, S52, POV_R - POV_X + 1, large_font(),
            fallback=CARD_TITLE)
        put_text(f, POV_X, POV_Y, words, cfg.rgb("attention"), large_font(), right=POV_R)
    return f


# =========================================================================================
# Idle: Arthur, the drawer and litter levels, and when he last went. Nearly still.
# =========================================================================================


def _last_visit(state: DisplayState, now: datetime) -> str:
    if state.last_visit_at is None:
        return "NO VISIT"
    return f"{fmt_age(seconds_between(state.last_visit_at, now))} AGO"


#: Length of each idle level bar (both the same), after its 5-px mark and a 2-px gap.
LEVEL_BAR_W = C52_W - 7


def _levels_52(
    f: Frame, state: DisplayState, cfg: Config, y_bin: int, y_lit: int, x: int = C52_X
) -> None:
    """Two labelled gauges, each 3 rows tall: a tiny poop and the drawer level, a tiny sand
    pile and the litter level. Marks at ``x``, bars from ``x + 7``."""
    draw_art(f, BIN_MARK, x, y_bin, cfg)
    gauge(f, x + 7, y_bin, LEVEL_BAR_W, 3, drawer_frac(state), drawer_role(state, cfg), cfg)
    draw_art(f, LITTER_MARK, x, y_lit, cfg)
    gauge(f, x + 7, y_lit, LEVEL_BAR_W, 3, litter_frac(state), litter_role(state), cfg)


#: Idle Arthur's content: bars and bottom line shifted right into the freed columns, so his
#: tail has columns 16-18 (column 19 stays a gap).
IDLE_X = C52_X + 3
IDLE_R = IDLE_X + 7 + LEVEL_BAR_W - 1  # 50: the last column inside the border
#: Arthur's long tail, drawn over the 16x16 icon from (TAIL_X, TAIL_Y). It continues the
#: little tail already in the icon art (bottom right of every arthur_sit pose) out into
#: columns 15-18, the gap between his body and the content (column 19 stays empty). Solid
#: orange (stripe bands on a 1-px tail read as gaps) with a dark tip. Every pose is the same
#: tail bending: the same number of pixels and the same dark tip, so it never grows.
TAIL_X = 13
TAIL_Y = 1
#: At rest: along the floor, then up and curled back at the tip, beside the bottom line.
TAIL_REST = (
    "......",  # row 1
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
    "....S.",  # row 10: the dark tip, curling back
    ".....O",
    ".....O",
    "....O.",
    "..OO..",  # row 14: along the floor
)
#: The wag, halfway: off the floor and leaning up, the tip a row higher.
TAIL_HALF = (
    "......",  # row 1
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
    "....S.",  # row 9: the dark tip
    ".....O",
    ".....O",
    "....O.",
    "...O..",
    "..O...",  # row 14
)
#: The wag, lifted: raised up beside the bottom line, the tip curled back at row 8.
TAIL_LIFT = (
    "......",  # row 1
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
    "....S.",  # row 8: the dark tip
    ".....O",
    "....O.",
    "....O.",
    "...O..",
    "..O...",
    "......",  # row 14
)

#: Idle Arthur's poses: name → (icon, tail art).
IDLE_POSES: dict[str, tuple[str, tuple[str, ...]]] = {
    "rest": ("arthur_sit", TAIL_REST),
    "blink": ("arthur_blink", TAIL_REST),
    "left": ("arthur_sit_left", TAIL_REST),
    "right": ("arthur_sit_right", TAIL_REST),
    "ear": ("arthur_sit_ear", TAIL_REST),
    "squint": ("arthur_sit_squint", TAIL_REST),
    "yawn": ("arthur_sit_yawn", TAIL_REST),
    "half": ("arthur_sit", TAIL_HALF),
    "lift": ("arthur_sit", TAIL_LIFT),
}
#: The frames of one tail wag, in order: up, dipping halfway down, up again and held; the
#: next second (a rest) brings it back down to the floor.
IDLE_WAG: tuple[str, ...] = ("lift", "half", "lift", "lift")

_R, _W = "rest", IDLE_WAG
#: Idle Arthur, one pose per second of the wall-clock minute. Alive, not busy: about two
#: thirds of the minute he sits still; between, one short gesture at a time, unevenly spaced,
#: with a still second either side (a glance left may run straight into one right). The first
#: half-minute (what the studio's All states tile plays) has a blink, a glance, an ear twitch
#: and a whole wag.
IDLE_SCRIPT: tuple[str, ...] = (
    _R, _R, _R, "blink", _R, _R, _R, "left", "right", _R,          # 0-9
    _R, _R, *_W, _R, _R, _R, "ear",                               # 10-19
    _R, _R, _R, _R, "blink", _R, _R, _R, _R, _R,                  # 20-29 (26-27: yawn)
    _R, "squint", "squint", _R, _R, _R, "right", _R, _R, _R,      # 30-39
    _R, *_W, _R, _R, _R, "blink", _R,                             # 40-49
    _R, _R, "ear", _R, _R, "left", _R, _R, _R, _R,                # 50-59
)
#: The rare yawn: in one minute out of every ``YAWN_EVERY_MIN`` (minute ``YAWN_MINUTE`` of
#: each cycle), these seconds of the script's resting stretch become a two-second yawn.
YAWN_EVERY_MIN = 5
YAWN_MINUTE = 3
YAWN_SECONDS = (26, 27)


def idle_pose(now: datetime) -> str:
    """Idle Arthur's pose at ``now`` (a key of :data:`IDLE_POSES`): the minute's script, with
    a yawn in place of a rest once every few minutes."""
    k = tick(now)
    s = k % len(IDLE_SCRIPT)
    if (k // 60) % YAWN_EVERY_MIN == YAWN_MINUTE and s in YAWN_SECONDS:
        return "yawn"
    return IDLE_SCRIPT[s]


def idle_tail(pose: str) -> tuple[str, ...]:
    """The long-tail art for an idle pose."""
    return IDLE_POSES[pose][1]


#: Words for the laser notice on idle's bottom line; the first that fits is used.
LASER_WORDS = ("WIPE LASER", "LASER DIRTY", "LASER")


@layout(S52, "ready", "arthur")
def _ready_arthur_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon, tail = IDLE_POSES[idle_pose(now)]
    icon52(f, cfg, icon)
    draw_art(f, tail, TAIL_X, TAIL_Y, cfg)
    _levels_52(f, state, cfg, 1, 5, x=IDLE_X)
    if state.laser_dirty:
        line = first_fitting(LASER_WORDS, IDLE_R - IDLE_X + 1, small_font())
        small52(f, cfg, line, "attention", x=IDLE_X, right=IDLE_R)
    else:
        small52(f, cfg, _last_visit(state, now), "text", x=IDLE_X, right=IDLE_R)
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
    # Blinks on the same seconds as idle Arthur's script (only the blink; no other gestures).
    icon52(f, cfg, "arthur_blink" if idle_pose(now) == "blink" else "arthur_sit")
    draw_art(f, BIN_MARK, C52_X, 3, cfg)
    big52(f, cfg, fmt_pct(state.drawer_pct), drawer_role(state, cfg), x=C52_X + 7)
    draw_art(f, LITTER_MARK, C52_X, 11, cfg)
    small52(f, cfg, fmt_pct(state.litter_pct), litter_role(state), x=C52_X + 7)
    return f


# =========================================================================================
# Public API
# =========================================================================================


def _norm_size(size: Size | str | None, cfg: Config) -> Size:
    if size is None:
        return cfg.size
    if isinstance(size, str):
        return parse_size(size)
    s = (int(size[0]), int(size[1]))
    if s not in list_sizes():
        raise ValueError(f"unsupported size {s}")
    return s


def list_kinds() -> tuple[str, ...]:
    """Every display kind, in layer order."""
    return KINDS


def list_sizes() -> tuple[Size, ...]:
    """The sizes that have layouts: only 52x16."""
    return (S52,)


def list_variants(kind: str, size: Size | str) -> list[str]:
    """Variant names for ``kind`` at ``size``; the first is the default."""
    s = parse_size(size) if isinstance(size, str) else tuple(size)
    try:
        return list(_REGISTRY[(s, kind)])  # type: ignore[index]
    except KeyError:
        raise KeyError(f"no layout for {kind!r} at {s}") from None


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
        return fn(state, now, cfg)
    with strict_text() as records:
        frame = fn(state, now, cfg)
        check_text(frame, records)
    return frame
