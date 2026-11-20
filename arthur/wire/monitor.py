"""The connection policy of ``arthur.sources.whisker.WhiskerSource``, around our own client.

:class:`Monitor` connects (token, then robot), keeps the live subscription running beside a
poll every ``poll_s`` (never under 120 s), re-reads the cached record every 15 minutes as a
heartbeat, reads the history at start, every six hours and when asked, and classifies every
failure the same way the feeder does:

- ``auth``: retried after 600 s, doubling to an hour (a rejected login is never retried fast);
- ``api``, ``timeout``, ``no_robots``: retried after 30 s, doubling to 600 s;
- a poll that fails waits at least 120 s; a poll refused for its token sends the monitor
  back to connect (a renewed token, else the password), as the feeder does;
- the error clears with the next contact (a pushed or polled update).

It hands on what the feeder's source needs and nothing more: ``on_record(record, source)``
with source ``start``, ``ws``, ``poll`` or ``heartbeat`` (the same record dicts and sources
``arthur.spikelog.SpikeLogger.observe`` produces), ``on_activity(activities)`` after each
history read, ``on_error(kind, where, message, retry_in)`` and ``on_info(event, text)``. The
on-clock source wraps it with the tracker as ``WhiskerSource`` wraps pylitterbot.
"""

import asyncio

from arthur.wire import errors, util
from arthur.wire.const import (
    API_BACKOFF_MAX,
    API_BACKOFF_START,
    AUTH_BACKOFF_MAX,
    AUTH_BACKOFF_START,
    HEARTBEAT_S,
    MIN_POLL_S,
    POLL_S,
)

TICK_S = 1.0
ACTIVITY_S = 6 * 3600.0
FIRST_ACTIVITY_WINDOW_S = 72 * 3600.0


class Backoff:
    def __init__(self, start, maximum):
        self.start = start
        self.maximum = maximum
        self._next = start

    def next(self):
        delay = self._next
        self._next = min(self._next * 2, self.maximum)
        return delay

    def reset(self):
        self._next = self.start


async def sleep_or_stop(stop, seconds):
    """Sleep, waking early if ``stop`` is set. True if stopping."""
    if stop.is_set():
        return True
    try:
        await asyncio.wait_for(stop.wait(), max(0.0, seconds))
    except Exception as exc:  # asyncio.TimeoutError on both Pythons
        if errors.kind_of(exc) != errors.TIMEOUT:
            raise
        return False
    return True


def _noop(*args):
    return None


class Monitor:
    def __init__(self, client, on_record, on_error=None, on_info=None, on_activity=None,
                 poll_s=POLL_S, mono=util.mono, now=util.now, sleep=sleep_or_stop,
                 heartbeat_s=HEARTBEAT_S, activity_s=ACTIVITY_S, history_limit=100):
        self.client = client
        self.on_record = on_record
        self.on_error = on_error or _noop
        self.on_info = on_info or _noop
        self.on_activity = on_activity or _noop
        self.poll_s = poll_s
        self.mono = mono
        self.now = now
        self.sleep = sleep
        self.heartbeat_s = heartbeat_s
        self.activity_s = activity_s
        self.history_limit = history_limit
        self.error = None
        self.last_contact = None  # wall time of the last pushed or polled update
        self.connected = False
        self._history_due = False
        self._newest_activity = None

    @property
    def poll_every(self):
        return max(float(self.poll_s), MIN_POLL_S)

    def want_history(self):
        """Ask for a history read soon (a gap in watching, a clean no visit explains)."""
        self._history_due = True

    def _fail(self, kind, where, exc, retry_in):
        self.error = kind
        self.on_error(kind, where, str(exc), retry_in)

    def _contact(self, source):
        if source != "heartbeat":
            self.last_contact = self.now()
            self.error = None
        self.on_record(self.client.record(), source)

    async def run(self, stop):
        """Hold the connection until ``stop`` is set. Never returns on failure."""
        auth_backoff = Backoff(AUTH_BACKOFF_START, AUTH_BACKOFF_MAX)
        api_backoff = Backoff(API_BACKOFF_START, API_BACKOFF_MAX)
        while not stop.is_set():
            try:
                await self.client.connect()
            except Exception as exc:  # noqa: BLE001 - every failure is classified
                kind = errors.kind_of(exc)
                delay = auth_backoff.next() if kind == errors.AUTH else api_backoff.next()
                self._fail(kind, "connect", exc, delay)
                if await self.sleep(stop, delay):
                    return
                continue
            auth_backoff.reset()
            api_backoff.reset()
            self.connected = True
            self.on_info("connected", "")
            live = asyncio.create_task(self.client.live(self._pushed, self.on_info))
            try:
                await self._watch(stop)
            finally:
                self.connected = False
                live.cancel()
                try:
                    await live
                except BaseException as exc:  # noqa: BLE001 - CancelledError, or its failure
                    self.on_info("live_ended", type(exc).__name__)

    def _pushed(self):
        self._contact("ws")

    async def _watch(self, stop):
        """Watch the connected robot until ``stop``, or until its token is refused."""
        self._contact("start")
        poll_backoff = Backoff(API_BACKOFF_START, API_BACKOFF_MAX)
        activity_backoff = Backoff(API_BACKOFF_START, API_BACKOFF_MAX)
        mono = self.mono
        next_poll = mono() + self.poll_every
        next_heartbeat = mono() + self.heartbeat_s
        next_activity = mono()
        activity_tried = None
        activity_failing = False
        while not stop.is_set():
            now = mono()
            if self._history_due and not activity_failing:
                earliest = now if activity_tried is None else max(now,
                                                                  activity_tried + MIN_POLL_S)
                next_activity = min(next_activity, earliest)
            if now >= next_activity:
                activity_tried = now
                try:
                    start = self._newest_activity
                    if start is None:
                        start = self.now() - FIRST_ACTIVITY_WINDOW_S
                    acts = await self.client.history(start=start, limit=self.history_limit)
                    for a in acts:
                        if self._newest_activity is None or a.ts > self._newest_activity:
                            self._newest_activity = a.ts
                    activity_backoff.reset()
                    activity_failing = False
                    self._history_due = False
                    next_activity = mono() + self.activity_s
                    self.on_activity(acts)
                except Exception as exc:  # noqa: BLE001
                    activity_failing = True
                    delay = activity_backoff.next()
                    self.on_error(errors.kind_of(exc), "activity", str(exc), delay)
                    next_activity = mono() + max(delay, self.poll_every)
            if now >= next_poll:
                try:
                    await self.client.refresh_robot()
                    self._contact("poll")
                    poll_backoff.reset()
                    next_poll = mono() + self.poll_every
                except Exception as exc:  # noqa: BLE001
                    kind = errors.kind_of(exc)
                    if kind == errors.AUTH:
                        self._fail(kind, "poll", exc, None)
                        return
                    delay = max(poll_backoff.next(), MIN_POLL_S)
                    self._fail(kind, "poll", exc, delay)
                    next_poll = mono() + delay
            if now >= next_heartbeat:
                self._contact("heartbeat")
                next_heartbeat = mono() + self.heartbeat_s
            if await self.sleep(stop, TICK_S):
                return
