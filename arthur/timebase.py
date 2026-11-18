"""The show's time base: one beat of 1/SHOW_FPS seconds, lined up with the wall clock.

Every loop that draws frames (the feeder, the studio's feeder, the push test) wakes on the
same grid: the 1/SHOW_FPS boundaries of the clock it draws by, with every whole second a
boundary. A tick that runs long lands on a later boundary; the ticks it covered are dropped,
never queued.

Arithmetic is in whole microseconds (``datetime`` resolution), so frame numbers are exact.
A float version, ``floor((now.timestamp() - start.timestamp()) * fps)``, was off by one on
many frames at 10 fps.
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: Frames a second for scenes and transitions (D-0019). Measured on the TC002 on 2026-11-18:
#: at 8 a second the slowest push took 64 ms of a 125 ms budget, while 12 a second missed its
#: budget twice in 120 (docs/reference/awtrix-ng.md, "How fast it takes frames").
SHOW_FPS = 8

_US_PER_S = 1_000_000
_ONE_US = timedelta(microseconds=1)


def frames_since(start: datetime, now: datetime, fps: int = SHOW_FPS) -> int:
    """Whole frames from ``start`` to ``now`` at ``fps``, floored (negative before ``start``)."""
    us = (now - start) // _ONE_US
    return (us * fps) // _US_PER_S


def until_next_tick(now: datetime, fps: int = SHOW_FPS) -> float:
    """Seconds from ``now`` to the next 1/fps boundary of its clock, in (0, 1/fps].

    Boundaries are k/fps seconds after each whole second, so every whole second is one. On a
    boundary the answer is a full 1/fps: the tick at that boundary is the one being run.
    """
    us = now.microsecond
    next_k = (us * fps) // _US_PER_S + 1
    return (next_k * _US_PER_S - us * fps) / (fps * _US_PER_S)
