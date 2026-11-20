"""The idle show's schedule (D-0019): which page or scene idle should play at a moment.

Pure: no I/O, no system clock, no pixels. Mapping calls it for ``ready`` and carries the
answer on ``DisplayState.cue``; the renderer only plays it.

A round is ``R = int(round_s)`` whole seconds, anchored to the Unix epoch, so every whole
hour (and every whole minute that is a multiple of ``R``) starts a round on the home page.
Inside a round::

    home (first half of the home time) | page | home (the rest) | scene

``page_len = floor(R * page_pct / 100)`` and ``scene_len = floor(R * scene_pct / 100)``; the
home page gets the remaining ``home`` seconds, ``home // 2`` before the page and the rest
after it. With 180 / 30 / 20: home 0-45, page 45-99, home 99-144, scene 144-180. A share of
0 removes its slot.

The night (quiet hours) is decided here too: :func:`asleep_since` and :func:`woke_at` give the
instants mapping carries on ``DisplayState`` so the renderer can time Arthur's sleep.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from arthur.compat import decimal_ratio
from arthur.model import Cue

if TYPE_CHECKING:
    from datetime import date

    from arthur.config import Config

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_S = timedelta(seconds=1)


def _share(length: int, pct: float) -> int:
    """``floor(length * pct / 100)``, exact for a decimal ``pct`` like 33.3: ``pct`` is read
    as the decimal it prints as (``int(Fraction(str(pct)) * length / 100)``, in integers)."""
    num, den = decimal_ratio(pct)
    return (num * length) // (den * 100)


def slots(cfg: Config) -> tuple[int, list[tuple[str, int, int]]]:
    """``(R, [(kind, start, length), ...])``: the round length and its non-home slots."""
    idle = cfg.idle
    r = int(idle.round_s)
    if r <= 0:
        return r, []
    page_len = _share(r, idle.page_pct)
    scene_len = _share(r, idle.scene_pct)
    home = r - page_len - scene_len
    out: list[tuple[str, int, int]] = []
    if page_len > 0:
        out.append(("page", home // 2, page_len))
    if scene_len > 0:
        out.append(("scene", r - scene_len, scene_len))
    return r, out


def is_quiet(now: datetime, cfg: Config) -> bool:
    """True inside quiet hours (the cat asleep), judged by the local wall-clock hour."""
    start, end = cfg.idle.quiet_from_h, cfg.idle.quiet_until_h
    if start == end:
        return False
    h = cfg.local(now).hour
    if start < end:
        return start <= h < end
    return h >= start or h < end  # wraps midnight


#: Seconds after the quiet hours end that mapping keeps ``DisplayState.woke_at`` set: long
#: enough for the renderer's waking (eyes open, then the yawn; see ``screens.WAKE_SCRIPT``).
WAKE_S = 4


def _at_local_hour(day: date, hour: int, cfg: Config) -> datetime:
    """``hour``:00 on the local ``day``, as an aware UTC instant (``Config.at_local_hour``):
    right across a clock change; an hour the change skips is read with the offset in force
    before it (zoneinfo's ``fold=0``)."""
    return cfg.at_local_hour(day, hour)


def quiet_window(now: datetime, cfg: Config) -> tuple[datetime, datetime] | None:
    """``(start, end)`` of the quiet hours containing ``now``, as aware UTC instants; None
    outside quiet hours or when there are none. A night that crosses a clock change is an
    hour longer or shorter than usual, as the wall clock says."""
    if not is_quiet(now, cfg):
        return None
    start_h, end_h = cfg.idle.quiet_from_h, cfg.idle.quiet_until_h
    local = cfg.local(now)
    today = local.date()
    one_day = timedelta(days=1)
    if start_h < end_h:
        return _at_local_hour(today, start_h, cfg), _at_local_hour(today, end_h, cfg)
    if local.hour >= start_h:  # the evening part of a window that wraps midnight
        return _at_local_hour(today, start_h, cfg), _at_local_hour(today + one_day, end_h, cfg)
    return _at_local_hour(today - one_day, start_h, cfg), _at_local_hour(today, end_h, cfg)


def last_quiet_end(now: datetime, cfg: Config) -> datetime | None:
    """The latest instant at or before ``now`` when quiet hours ended; None with none."""
    if cfg.idle.quiet_from_h == cfg.idle.quiet_until_h:
        return None
    today = cfg.local(now).date()
    end = _at_local_hour(today, cfg.idle.quiet_until_h, cfg)
    if end > now:
        end = _at_local_hour(today - timedelta(days=1), cfg.idle.quiet_until_h, cfg)
    return end


def asleep_since(since: datetime | None, now: datetime, cfg: Config) -> datetime | None:
    """When idle Arthur's current sleep began: in quiet hours, the later of their start and
    the moment idle began (``since``; None counts as long ago). None outside quiet hours."""
    window = quiet_window(now, cfg)
    if window is None:
        return None
    start = window[0]
    return start if since is None or since < start else since


def woke_at(since: datetime | None, now: datetime, cfg: Config) -> datetime | None:
    """When the quiet hours ended, for :data:`WAKE_S` seconds after it, if Arthur was asleep
    then (idle began before the end; None counts as long ago). None otherwise."""
    end = last_quiet_end(now, cfg)
    if end is None or (now - end).total_seconds() >= WAKE_S:
        return None
    if since is not None and since >= end:
        return None  # idle began after the night: he was never asleep
    return end


def idle_cue(since: datetime | None, now: datetime, cfg: Config) -> Cue | None:
    """The page or scene to play at ``now`` when idle began at ``since``; None for home.

    A slot plays only when it began at or after ``since + settle_s``, so nothing starts
    part-way through; a scene slot that begins in quiet hours is skipped.
    """
    if since is None:
        return None
    r, round_slots = slots(cfg)
    if not round_slots:
        return None
    secs = (now - _EPOCH) // _ONE_S  # whole seconds, floored, exact
    n, pos = divmod(secs, r)
    for kind, start, length in round_slots:
        if start <= pos < start + length:
            at = _EPOCH + timedelta(seconds=n * r + start)
            if at < since + timedelta(seconds=cfg.idle.settle_s):
                return None
            if kind == "scene" and is_quiet(at, cfg):
                return None
            return Cue(kind=kind, at=at, length_s=float(length), n=n)
    return None
