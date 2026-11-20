"""Time for the program on the clock: the wall clock, a monotonic clock, sleeping and waiting.

Everything in :mod:`arthur.onclock` takes its time from one :class:`Clock`, so tests can run
the program on a fake or a sped-up clock. On the clock itself the wall time is the system's
(the TC002's time was exact when checked, ``docs/reference/awtrix-ng.md``) and the monotonic
time is :data:`arthur.wire.util.mono`, which does not wrap.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

from arthur.wire import util

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ONE_US = timedelta(microseconds=1)

if util.IS_MPY:
    def stamp_us():
        """A microsecond counter for timing work (it wraps; use :func:`since_us`)."""
        return time.ticks_us()

    def since_us(t0):
        """Microseconds since ``t0`` (a :func:`stamp_us`)."""
        return time.ticks_diff(time.ticks_us(), t0)
else:
    def stamp_us():
        return time.perf_counter_ns() // 1000

    def since_us(t0):
        return time.perf_counter_ns() // 1000 - t0


def epoch_s(dt):
    """Seconds since 1970 for an aware datetime, exact to the microsecond."""
    return ((dt - EPOCH) // ONE_US) / 1_000_000


def _system_wall():
    return datetime.now(UTC)


class Clock:
    """``wall()`` is an aware UTC datetime, ``mono()`` seconds that never go back,
    ``sleep(s)`` an awaitable pause.

    With only ``wall_fn`` given (tests), ``mono`` is the wall time in seconds, so the two
    agree on a fake clock.
    """

    def __init__(self, wall_fn=None, sleep=None, time_fn=None):
        self.wall = wall_fn or _system_wall
        if time_fn is not None:
            self.mono = time_fn
        elif wall_fn is not None:
            self.mono = lambda: epoch_s(wall_fn())
        else:
            self.mono = util.mono
        self.sleep = sleep or asyncio.sleep

    def epoch(self):
        """The wall time in seconds since 1970."""
        return epoch_s(self.wall())

    async def wait(self, stop, seconds, step=1.0):
        """Sleep ``seconds`` or until ``stop`` (an ``asyncio.Event``) is set, whichever comes
        first, looking at ``stop`` at least every ``step`` seconds. True if stopped."""
        end = self.mono() + seconds
        while not stop.is_set():
            left = end - self.mono()
            if left <= 0:
                return False
            await self.sleep(min(left, step))
        return True
