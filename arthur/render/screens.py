"""Layouts: DisplayState + now + config → Frame, at 32x8 and 52x16.

A pure function of its inputs; knows nothing about AWTRIX. Every display kind has at least one
layout at both sizes; some have named variants. The first variant registered for a
(size, kind) is the default.

Canvas rules (docs/states.md, design-screen skill):

- The clock's stale border covers row 0, the last row, column 0 and the last column, so
  nothing essential is drawn there.
- 32x8: 8x8 icon at x=0, text from x=9 with capitals on rows 1-5, optional gauge on row 6,
  badges on column 30.
- 52x16: 16x16 icon at x=0, content in x=17..47, badges in columns 49-50.
- About one frame a second: time-dependent text and 2-frame blinks are derived from ``now``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime

from arthur.config import Config, parse_size
from arthur.model import KINDS, DisplayState
from arthur.render.font import Font, large_font, small_font
from arthur.render.frame import Frame
from arthur.render.icons import draw_icon

Size = tuple[int, int]
Layout = Callable[[DisplayState, datetime, Config], Frame]

S32: Size = (32, 8)
S52: Size = (52, 16)

# 32x8 geometry
T32_X = 9  # text starts here (after the 8x8 icon and a 1-px gap)
T32_R = 29  # last column text may use (column 30 holds badges, 31 is the border)
T32_Y = 1  # top row of 5-px capitals
G32_Y = 6  # gauge row

# 52x16 geometry
C52_X = 17  # content starts here (after the 16x16 icon and a 1-px gap)
C52_R = 47  # last content column (49-50 hold badges, 51 is the border)
C52_W = C52_R - C52_X + 1

_REGISTRY: dict[tuple[Size, str], dict[str, Layout]] = {}


def layout(size: Size, kind: str, variant: str = "default") -> Callable[[Layout], Layout]:
    """Register a layout function for (size, kind) under ``variant``."""

    def deco(fn: Layout) -> Layout:
        _REGISTRY.setdefault((size, kind), {})[variant] = fn
        return fn

    return deco


# --- small helpers ----------------------------------------------------------------------


def blink(now: datetime) -> bool:
    """True on even seconds: drives 2-frame blinks at about one frame a second."""
    return int(now.timestamp()) % 2 == 0


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


def drawer_role(state: DisplayState, cfg: Config) -> str:
    if state.kind == "drawer_full" or (state.drawer_pct or 0) >= 100:
        return "alert"
    if (state.drawer_pct or 0) >= cfg.thresholds.drawer_warn_pct:
        return "attention"
    return "ok"


def litter_role(state: DisplayState) -> str:
    st = (state.litter_state or "").upper()
    if st == "EMPTY":
        return "alert"
    if st == "LOW":
        return "attention"
    return "ok"


def fit(text: str, max_w: int, font: Font, scale: int = 1) -> str:
    """Trim ``text`` until it fits ``max_w`` pixels."""
    while text and font.width(text, scale) > max_w:
        text = text[:-1]
    return text


def gauge(f: Frame, x: int, y: int, w: int, h: int, frac: float, role: str, cfg: Config) -> None:
    """A horizontal bar: ``frac`` of it lit in ``role`` over a dim track."""
    frac = max(0.0, min(1.0, frac))
    f.rect(x, y, w, h, cfg.rgb("dim"))
    lit = round(frac * w)
    if frac > 0 and lit == 0:
        lit = 1
    f.rect(x, y, lit, h, cfg.rgb(role))


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


# --- 32x8 building blocks -----------------------------------------------------------------


def _f32() -> Frame:
    return Frame(*S32)


def _icon_text_32(
    state: DisplayState,
    cfg: Config,
    icon: str,
    accent: str,
    text: str,
    text_role: str = "text",
    *,
    icon_override: str | None = None,
    fill: float = 0.0,
    fill_role: str | None = None,
) -> Frame:
    f = _f32()
    draw_icon(
        f, icon, 0, 0, cfg, accent=accent, override=icon_override, fill=fill, fill_role=fill_role
    )
    font = small_font()
    f.text(T32_X, T32_Y, fit(text, T32_R - T32_X + 1, font), cfg.rgb(text_role), font)
    return f


def _gauge_32(f: Frame, frac: float, role: str, cfg: Config) -> None:
    gauge(f, T32_X, G32_Y, T32_R - T32_X + 1, 1, frac, role, cfg)


def _centered_32(f: Frame, text: str, role: str, cfg: Config) -> None:
    font = small_font()
    text = fit(text, 30, font)
    x = 1 + (30 - font.width(text)) // 2
    f.text(x, T32_Y, text, cfg.rgb(role), font)


# --- 52x16 building blocks ----------------------------------------------------------------


def _f52() -> Frame:
    return Frame(*S52)


def _icon_52(f: Frame, cfg: Config, icon: str, accent: str, **kw: object) -> None:
    draw_icon(f, icon, 0, 0, cfg, size=16, accent=accent, **kw)  # type: ignore[arg-type]


def _title_52(f: Frame, cfg: Config, title: str, role: str, sub: str | None = None,
              sub_role: str = "text") -> None:
    """Large-font title on rows 1-7, optional small-font line on rows 9-13."""
    big = large_font()
    f.text(C52_X, 1, fit(title, C52_W, big), cfg.rgb(role), big)
    if sub:
        sm = small_font()
        f.text(C52_X, 9, fit(sub, C52_W, sm), cfg.rgb(sub_role), sm)


def _big_52(f: Frame, cfg: Config, text: str, role: str) -> None:
    """Double-size digits on rows 1-10."""
    sm = small_font()
    f.text(C52_X, 1, fit(text, C52_W, sm, 2), cfg.rgb(role), sm, scale=2)


def _drawer_gauge_52(f: Frame, state: DisplayState, cfg: Config) -> None:
    """The drawer gauge that live screens share at 52x16: rows 13-14."""
    gauge(f, C52_X, 13, C52_W, 2, (state.drawer_pct or 0) / 100, drawer_role(state, cfg), cfg)


# =========================================================================================
# Layer 1: trust
# =========================================================================================

_NO_DATA_REASON = {
    "auth": "LOGIN",
    "api": "API ERR",
    "no_robots": "NO ROBOT",
    "timeout": "TIMEOUT",
    "no_snapshot": "WAITING",
}


@layout(S32, "no_data")
def _no_data_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f32()
    _centered_32(f, "NO DATA", "trust", cfg)
    # A dotted rule under the words: a shape no other screen has.
    for x in range(3, 29, 2):
        f.pixel(x, G32_Y, cfg.rgb("trust"))
    return f


@layout(S52, "no_data")
def _no_data_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "cloud_x", "trust")
    _title_52(f, cfg, "NO DATA", "trust", _NO_DATA_REASON.get(state.detail or "", "WHISKER"))
    return f


@layout(S32, "robot_offline")
def _offline_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    age = fmt_age(seconds_between(state.last_seen, now))
    return _icon_text_32(state, cfg, "wifi_x", "alert", age)


@layout(S52, "robot_offline")
def _offline_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "wifi_x", "alert")
    age = fmt_age(seconds_between(state.last_seen, now))
    _title_52(f, cfg, "OFFLINE", "alert", f"SEEN {age}")
    return f


@layout(S32, "robot_off")
def _off_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, "power", "text", "OFF")


@layout(S52, "robot_off")
def _off_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "power", "text")
    _title_52(f, cfg, "OFF", "text", "SWITCHED")
    return f


# =========================================================================================
# Layer 2: needs you (alerts blink their icon; the words stay steady so they can be read)
# =========================================================================================


@layout(S32, "drawer_full")
def _drawer_full_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(
        state, cfg, "drawer", "alert" if blink(now) else "text", "FULL", "alert",
        fill=1.0, fill_role="alert",
    )


@layout(S52, "drawer_full")
def _drawer_full_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "drawer", "alert" if blink(now) else "text", fill=1.0, fill_role="alert")
    _title_52(f, cfg, "FULL", "alert", "EMPTY IT")
    return f


@layout(S32, "fault")
def _fault_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(
        state, cfg, "warning", "alert", state.fault_code or "?", "alert",
        icon_override=None if blink(now) else "off",
    )


@layout(S52, "fault")
def _fault_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    if blink(now):
        _icon_52(f, cfg, "warning", "alert")
    _title_52(f, cfg, "FAULT", "alert", state.fault_code or "?")
    return f


@layout(S32, "bonnet_removed")
def _bonnet_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, "bonnet", "attention", "LID", "attention")


@layout(S52, "bonnet_removed")
def _bonnet_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "bonnet", "attention")
    _title_52(f, cfg, "BONNET", "attention", "REMOVED")
    return f


# =========================================================================================
# Layer 3: live
# =========================================================================================

# --- in_use ---


@layout(S32, "in_use", "timer")
def _in_use_timer_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, "cat", "activity", fmt_clock(seconds_between(state.since, now)))


@layout(S32, "in_use", "blink")
def _in_use_blink_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(
        state, cfg, "cat", "activity" if blink(now) else "text",
        fmt_clock(seconds_between(state.since, now)), "activity",
    )


#: in_use "bar": seconds per lit pixel of the growing bar (21 px ≈ 3.5 minutes).
IN_USE_BAR_S_PER_PX = 10


@layout(S32, "in_use", "bar")
def _in_use_bar_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    secs = seconds_between(state.since, now) or 0
    f = _icon_text_32(state, cfg, "cat", "activity", fmt_clock(secs), "activity")
    _gauge_32(f, secs / (IN_USE_BAR_S_PER_PX * (T32_R - T32_X + 1)), "activity", cfg)
    return f


@layout(S52, "in_use", "timer")
def _in_use_timer_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "cat", "activity")
    _big_52(f, cfg, fmt_clock(seconds_between(state.since, now)), "text")
    _drawer_gauge_52(f, state, cfg)
    return f


@layout(S52, "in_use", "label")
def _in_use_label_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "cat", "activity")
    _title_52(f, cfg, "IN USE", "activity", fmt_clock(seconds_between(state.since, now)))
    return f


@layout(S52, "in_use", "blink")
def _in_use_blink_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "cat", "activity" if blink(now) else "text")
    _big_52(f, cfg, fmt_clock(seconds_between(state.since, now)), "activity")
    _drawer_gauge_52(f, state, cfg)
    return f


# --- waiting ---


def _wait_text(state: DisplayState, now: datetime) -> str:
    if state.sleeping:
        return "SLEEP"
    if state.deadline is None:
        return "WAIT"
    left = (state.deadline - now).total_seconds()
    return "DUE" if left <= 0 else fmt_countdown(left)


def _wait_icon(state: DisplayState) -> tuple[str, str]:
    return ("hourglass_sleep", "dim") if state.sleeping else ("hourglass", "cycle")


@layout(S32, "waiting", "countdown")
def _waiting_countdown_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    icon, accent = _wait_icon(state)
    return _icon_text_32(state, cfg, icon, accent, _wait_text(state, now), "cycle")


@layout(S32, "waiting", "bar")
def _waiting_bar_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    icon, accent = _wait_icon(state)
    if state.sleeping or state.deadline is None:
        return _icon_text_32(state, cfg, icon, accent, _wait_text(state, now), "cycle")
    left = (state.deadline - now).total_seconds()
    text = "DUE" if left <= 0 else f"{math.ceil(left / 60)}M"
    f = _icon_text_32(state, cfg, icon, accent, text, "cycle")
    _gauge_32(f, wait_fraction_left(state, now), "cycle", cfg)
    return f


@layout(S32, "waiting", "word")
def _waiting_word_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    icon, accent = _wait_icon(state)
    f = _icon_text_32(state, cfg, icon, accent, "SLEEP" if state.sleeping else "WAIT", "cycle")
    if not state.sleeping and state.deadline is not None:
        _gauge_32(f, wait_fraction_left(state, now), "cycle", cfg)
    return f


@layout(S52, "waiting", "countdown")
def _waiting_countdown_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon, accent = _wait_icon(state)
    _icon_52(f, cfg, icon, accent)
    if state.sleeping:
        _title_52(f, cfg, "SLEEP", "cycle", "NO CYCLE")
    else:
        _big_52(f, cfg, _wait_text(state, now), "cycle")
        _drawer_gauge_52(f, state, cfg)
    return f


@layout(S52, "waiting", "bar")
def _waiting_bar_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon, accent = _wait_icon(state)
    _icon_52(f, cfg, icon, accent)
    if state.sleeping:
        _title_52(f, cfg, "SLEEP", "cycle", "NO CYCLE")
        return f
    _title_52(f, cfg, "WAIT", "cycle", _wait_text(state, now))
    gauge(f, C52_X, 14, C52_W, 1, wait_fraction_left(state, now), "cycle", cfg)
    return f


@layout(S52, "waiting", "label")
def _waiting_label_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    icon, accent = _wait_icon(state)
    _icon_52(f, cfg, icon, accent)
    if state.sleeping:
        _title_52(f, cfg, "SLEEP", "cycle", "NO CYCLE")
    else:
        _title_52(f, cfg, "CLEAN IN", "cycle", _wait_text(state, now), "text")
    return f


# --- cleaning ---


def _spin(now: datetime) -> str:
    return "cycle_a" if blink(now) else "cycle_b"


def _clean_left(state: DisplayState, now: datetime) -> str:
    if state.cycle_end is None:
        return "--"
    return fmt_countdown((state.cycle_end - now).total_seconds())


@layout(S32, "cleaning", "progress")
def _cleaning_progress_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    frac = cycle_fraction(state, now)
    f = _icon_text_32(state, cfg, _spin(now), "cycle", fmt_pct(frac * 100), "cycle")
    _gauge_32(f, frac, "cycle", cfg)
    return f


@layout(S32, "cleaning", "countdown")
def _cleaning_countdown_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, _spin(now), "cycle", _clean_left(state, now), "cycle")


@layout(S32, "cleaning", "word")
def _cleaning_word_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _icon_text_32(state, cfg, _spin(now), "cycle", "CLEAN", "cycle")
    _gauge_32(f, cycle_fraction(state, now), "cycle", cfg)
    return f


@layout(S52, "cleaning", "progress")
def _cleaning_progress_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    frac = cycle_fraction(state, now)
    _icon_52(f, cfg, _spin(now), "cycle")
    _big_52(f, cfg, fmt_pct(frac * 100), "cycle")
    gauge(f, C52_X, 13, C52_W, 2, frac, "cycle", cfg)
    return f


@layout(S52, "cleaning", "countdown")
def _cleaning_countdown_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, _spin(now), "cycle")
    _big_52(f, cfg, _clean_left(state, now), "cycle")
    _drawer_gauge_52(f, state, cfg)
    return f


@layout(S52, "cleaning", "label")
def _cleaning_label_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, _spin(now), "cycle")
    _title_52(f, cfg, "CLEAN", "cycle", f"~{_clean_left(state, now)} LEFT")
    gauge(f, C52_X, 14, C52_W, 1, cycle_fraction(state, now), "cycle", cfg)
    return f


# --- interrupted: amber no-entry sign, blinking, no ring and no progress bar ---


@layout(S32, "interrupted")
def _interrupted_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _icon_text_32(
        state, cfg, "stop", "attention", "STOP", "attention",
        icon_override=None if blink(now) else "off",
    )
    for x in range(T32_X, T32_R + 1, 4):  # dashed amber rule: "broken"
        f.hline(x, G32_Y, 2, cfg.rgb("attention"))
    return f


@layout(S52, "interrupted")
def _interrupted_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    if blink(now):
        _icon_52(f, cfg, "stop", "attention")
    _title_52(f, cfg, "STOPPED", "attention", "CAT BACK IN")
    for x in range(C52_X, C52_R + 1, 4):
        f.hline(x, 14, 2, cfg.rgb("attention"))
    return f


# --- paused, emptying, powering ---


@layout(S32, "paused")
def _paused_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, "pause", "attention", "PAUSE", "attention")


@layout(S52, "paused")
def _paused_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "pause", "attention")
    _title_52(f, cfg, "PAUSED", "attention", fmt_clock(seconds_between(state.since, now)))
    return f


@layout(S32, "emptying")
def _emptying_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, "empty", "cycle", "EMPTY", "cycle")


@layout(S52, "emptying")
def _emptying_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "empty", "cycle")
    _title_52(f, cfg, "EMPTY", "cycle", fmt_clock(seconds_between(state.since, now)))
    _drawer_gauge_52(f, state, cfg)
    return f


@layout(S32, "powering")
def _powering_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    word = "DOWN" if state.detail == "down" else "START"
    return _icon_text_32(state, cfg, "power", "cycle" if blink(now) else "text", word, "cycle")


@layout(S52, "powering")
def _powering_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "power", "cycle" if blink(now) else "text")
    _title_52(f, cfg, "POWER", "cycle", "SHUTDOWN" if state.detail == "down" else "STARTING")
    return f


# --- just_cleaned ---


@layout(S32, "just_cleaned")
def _just_cleaned_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    return _icon_text_32(state, cfg, "check", "ok", "DONE", "ok")


@layout(S52, "just_cleaned")
def _just_cleaned_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    _icon_52(f, cfg, "check", "ok")
    _title_52(f, cfg, "CLEAN", "ok")
    _drawer_gauge_52(f, state, cfg)
    return f


# =========================================================================================
# Layer 4: idle
# =========================================================================================


@layout(S32, "ready", "drawer")
def _ready_drawer_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    role = drawer_role(state, cfg)
    frac = (state.drawer_pct or 0) / 100
    f = _icon_text_32(
        state, cfg, "drawer", "text", fmt_pct(state.drawer_pct), role, fill=frac, fill_role=role
    )
    _gauge_32(f, frac, role, cfg)
    return f


@layout(S32, "ready", "visit")
def _ready_visit_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    role = drawer_role(state, cfg)
    f = _icon_text_32(state, cfg, "cat", "ok", fmt_age(seconds_between(state.last_visit_at, now)))
    # visits today as dots under the text, drawer level as the dot colour
    n = min(state.visits_today or 0, 10)
    for i in range(n):
        f.pixel(T32_X + 2 * i, G32_Y, cfg.rgb(role))
    return f


@layout(S32, "ready", "litter")
def _ready_litter_32(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    role = drawer_role(state, cfg)
    frac = (state.drawer_pct or 0) / 100
    f = _f32()
    draw_icon(f, "drawer", 0, 0, cfg, accent="text", fill=frac, fill_role=role)
    sm = small_font()
    x = T32_X + f.text(T32_X, T32_Y, fmt_pct(state.drawer_pct)[:-1], cfg.rgb(role), sm)
    x += 2
    f.text(x, T32_Y, "L" + fmt_pct(state.litter_pct)[:-1], cfg.rgb(litter_role(state)), sm)
    return f


@layout(S52, "ready", "gauge")
def _ready_gauge_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    role = drawer_role(state, cfg)
    frac = (state.drawer_pct or 0) / 100
    _icon_52(f, cfg, "drawer", "text", fill=frac, fill_role=role)
    _big_52(f, cfg, fmt_pct(state.drawer_pct), role)
    _drawer_gauge_52(f, state, cfg)
    return f


@layout(S52, "ready", "summary")
def _ready_summary_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    role = drawer_role(state, cfg)
    frac = (state.drawer_pct or 0) / 100
    _icon_52(f, cfg, "drawer", "text", fill=frac, fill_role=role)
    last = fmt_age(seconds_between(state.last_visit_at, now))
    _title_52(f, cfg, fmt_pct(state.drawer_pct), role, f"LAST {last}")
    # visits today: one dot each on row 14
    for i in range(min(state.visits_today or 0, C52_W // 2)):
        f.pixel(C52_X + 2 * i, 14, cfg.rgb("text"))
    return f


@layout(S52, "ready", "litter")
def _ready_litter_52(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    f = _f52()
    role = drawer_role(state, cfg)
    lrole = litter_role(state)
    frac = (state.drawer_pct or 0) / 100
    _icon_52(f, cfg, "drawer", "text", fill=frac, fill_role=role)
    sm = small_font()
    f.text(C52_X, 1, "D " + fmt_pct(state.drawer_pct), cfg.rgb(role), sm)
    gauge(f, C52_X, 6, C52_W, 1, frac, role, cfg)
    f.text(C52_X, 8, "L " + fmt_pct(state.litter_pct), cfg.rgb(lrole), sm)
    gauge(f, C52_X, 13, C52_W, 1, (state.litter_pct or 0) / 100, lrole, cfg)
    return f


# =========================================================================================
# Badges: fixed slots so position as well as colour says which is which.
# =========================================================================================

#: badge → (slot index from the top, role)
BADGE_STYLE: dict[str, tuple[int, str]] = {
    "drawer_nearly_full": (0, "attention"),
    "litter_low": (1, "text"),
    "laser_dirty": (2, "cycle"),
    "on_battery": (3, "alert"),
    "polling_only": (4, "trust"),
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


def render(
    state: DisplayState,
    now: datetime,
    cfg: Config,
    size: Size | str | None = None,
    variant: str | None = None,
) -> Frame:
    """Draw ``state`` at ``now``. ``size`` defaults to the config's; ``variant`` to the
    kind's default. A variant name the kind does not have falls back to the default, so a
    caller can pick one variant name for every state."""
    s = _norm_size(size, cfg)
    variants = _REGISTRY.get((s, state.kind))
    if not variants:
        raise KeyError(f"no layout for {state.kind!r} at {s}")
    fn = variants.get(variant) if variant else None
    if fn is None:
        fn = next(iter(variants.values()))
    frame = fn(state, now, cfg)
    if state.kind != "no_data":
        draw_badges(frame, state.badges, cfg)
    return frame
