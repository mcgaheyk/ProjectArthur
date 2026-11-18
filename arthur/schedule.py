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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction

from arthur.config import Config
from arthur.model import Cue

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_S = timedelta(seconds=1)


def _share(length: int, pct: float) -> int:
    """``floor(length * pct / 100)``, exact for a decimal ``pct`` like 33.3."""
    return int(Fraction(str(pct)) * length / 100)


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
