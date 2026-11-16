"""Layouts: DisplayState + now + config → Frame, at 52x16 (the Ulanzi TC002).

A pure function of its inputs; knows nothing about AWTRIX. Every display kind has at least one
layout; some have named variants. The first variant registered for a kind is the default.

Arthur (an orange tabby) is the mascot; see design-screen section 0 for the direction.

Canvas rules (docs/states.md, design-screen skill):

- The clock's stale border covers row 0, row 15, column 0 and column 51, so nothing is drawn
  there. One deliberate exception: a layout registered with ``edge_ring=True`` (listed in
  :data:`EDGE_RING`; today only ``drawer_full``) may draw a 1-pixel ring on exactly those
  pixels. The stale overlay paints over the whole ring, so a stale frame still reads as
  stale, and such a layout keeps its other content one pixel clear of the ring (rows 2-13,
  columns 2-49).
- Every label goes through :func:`arthur.render.text.put_text` with the box it must fit, and
  nothing else may touch a label (the strict text check in the tests).
- A 16x16 picture at x=0, content in x=17..47 (a big-font or small-font first line from row 1,
  a small-font second line on rows 9-13 or the drawer gauge on rows 13-14). Columns 48-50 are
  not reserved: most layouts leave them empty, but the idle Arthur layout shifts its bars and
  bottom line to columns 20-50 (to make room for his long tail in columns 16-18), and
  ``drawer_full`` uses columns up to 49 inside its ring.
- Level colours: the drawer bar is ``drawer`` (orange) below ``drawer_warn_pct`` and
  ``alert`` from there or when full; the litter bar follows the robot's own litter word
  (OPTIMAL ``ok``, REFILL/OVERFILL ``caution``, LOW/EMPTY ``alert``, else ``sand``).
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


#: The poop's face, one entry per second (a 15 s loop). Mostly he sits smiling; now and then
#: he blinks, glances left and right, and winks once. The loop runs on its own, not with the
#: ring: its length is odd and the ring's period is 2 s, so each time round every face lands
#: on the other ring phase. Only the eyes move; the smile stays.
DRAWER_FULL_LOOP: tuple[str, ...] = (
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
    icon52(f, cfg, DRAWER_FULL_LOOP[phase(now, len(DRAWER_FULL_LOOP))])
    draw_art(f, FULL_ART, RING_X, 2, cfg)
    width = RING_R - RING_X + 1
    sub = caption(state, cfg, "drawer_full", S52, width, condensed_small_font(),
                  fallback="EMPTY IT")
    small52(f, cfg, sub, "text", y=9, x=RING_X, right=RING_R, condensed=True)
    return f


# =========================================================================================
# In use: the funny moment. Arthur and a big poop emoji take turns.
# =========================================================================================

#: In-use loop at 52x16, one entry per second. Page A (4 s): Arthur in the box giving a
#: "do you mind?" look (eyes dart left, right, then squeeze shut with a sweat drop), big
#: timer and drawer bar. Page B (2 s): the big poop emoji with wiggling stink lines, the
#: caption as the headline and the timer small underneath.
IN_USE_LOOP: tuple[str, ...] = (
    "arthur_box_left",
    "arthur_box_right",
    "arthur_box_shut",
    "arthur_box_shut",
    "poop",
    "poop",
)


def _in_use_timer(state: DisplayState, now: datetime) -> str:
    return fmt_clock(seconds_between(state.since, now))


@layout(S52, "in_use", "duo")
def _in_use_duo_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    p = phase(now, len(IN_USE_LOOP))
    pose = IN_USE_LOOP[p]
    if pose != "poop":
        icon52(f, cfg, pose)
        digits52(f, cfg, _in_use_timer(state, now), "text")
        drawer_gauge52(f, state, cfg)
        return f
    icon52(f, cfg, "poop_big")
    a, b = (STINK_A, STINK_B) if p % 2 == 0 else (STINK_B, STINK_A)
    draw_art(f, a[:3], 1, 1, cfg)
    draw_art(f, b[:3], 10, 1, cfg)
    cap = caption(state, cfg, "in_use", S52, C52_W, large_font(), fallback="BUSY")
    big52(f, cfg, cap, "fur")
    small52(f, cfg, _in_use_timer(state, now), "text")
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
    icon52(f, cfg, "arthur_box")
    digits52(f, cfg, _in_use_timer(state, now), "text")
    drawer_gauge52(f, state, cfg)
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
# Cleaning: the globe turning, in mint
# =========================================================================================


def drum(f: Frame, cfg: Config, now: datetime) -> None:
    """The robot's globe: a mint ring whose opening turns a step every second, with litter
    lying in the bottom."""
    size = PIC
    steps = 8
    c = (size - 1) / 2
    r = 6.4
    theta = phase(now, steps) * 2 * math.pi / steps - math.pi / 2
    gap = math.radians(38)
    mint, sand = cfg.rgb("mint"), cfg.rgb("sand")
    for y in range(size):
        for x in range(size):
            dx, dy = x - c, y - c
            d = math.hypot(dx, dy)
            if abs(d - r) <= 0.6:
                diff = (math.atan2(dy, dx) - theta + math.pi) % (2 * math.pi) - math.pi
                if abs(diff) > gap:
                    f.pixel(x, y, mint)
            elif d < r - 1.4 and dy > 1.5:
                f.pixel(x, y, sand)


def _clean_left(state: DisplayState, now: datetime) -> str:
    if state.cycle_end is None:
        return "--"
    return fmt_countdown((state.cycle_end - now).total_seconds())


@layout(S52, "cleaning", "spin")
def _cleaning_spin_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    drum(f, cfg, now)
    big52(f, cfg, _clean_left(state, now), "text")
    cap = caption(state, cfg, "cleaning", S52, C52_W, small_font(), fallback="CLEANING")
    small52(f, cfg, cap, "mint")
    return f


@layout(S52, "cleaning", "gauge")
def _cleaning_gauge_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    drum(f, cfg, now)
    digits52(f, cfg, _clean_left(state, now), "mint")
    drawer_gauge52(f, state, cfg)
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
#: little tail already in the icon art (bottom right of arthur_sit / arthur_blink, raised in
#: arthur_tail) out into columns 16-18. Solid orange (stripe bands on a 1-px tail read as gaps)
#: with a dark tip.
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
#: Flicked: up from the raised tail in arthur_tail, out to the right and curled back at the
#: tip, the same curl as at rest but tall, beside the level bars.
TAIL_FLICK = (
    "....S.",  # row 1: the dark tip, curling back
    ".....O",
    ".....O",
    "....O.",
    "....O.",
    "...O..",
    "...O..",
    "..O...",  # row 8: just above the raised tail in the icon
    "......",
    "......",
    "......",
    "......",
    "......",
    "......",
)
#: Words for the laser notice on idle's bottom line; the first that fits is used.
LASER_WORDS = ("WIPE LASER", "LASER DIRTY", "LASER")


def idle_tail(pose: str | None) -> tuple[str, ...]:
    """The long-tail art for an idle pose (see :func:`idle_pose`)."""
    return TAIL_FLICK if pose == "tail" else TAIL_REST


@layout(S52, "ready", "arthur")
def _ready_arthur_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    pose = idle_pose(now, cfg)
    icon52(f, cfg, {"blink": "arthur_blink", "tail": "arthur_tail"}.get(pose or "", "arthur_sit"))
    draw_art(f, idle_tail(pose), TAIL_X, TAIL_Y, cfg)
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
    icon52(f, cfg, "arthur_blink" if idle_pose(now, cfg) == "blink" else "arthur_sit")
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
