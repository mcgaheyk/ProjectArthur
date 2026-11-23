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
history read, ``on_error(kind, where, message, retry_in)`` (the exception itself is
``last_exc``) and ``on_info(event, text)``. :class:`arthur.sources.wire_source.WireSource`
wraps it with the tracker as ``WhiskerSource`` wraps pylitterbot, through three more hooks:
``connect`` (an async callable used instead of ``client.connect``), ``before_tick()`` at the
top of each turn of the loop, and ``after_history()`` (async) after its history step.

A history read that is due (:meth:`Monitor.want_history`) and allowed is made at once, not a
tick later, as the feeder's source does.

**Holding a routine poll back** (the clock only). A poll opens a TLS connection, and on the
clock its handshake holds the program's loop about 0.3 s, so a poll that lands in a page or
scene shows as a hitch. A poll is held only while the live subscription is acknowledged and
up (``live_up``: set on ``ws_start_ack``, cleared on ``ws_closed`` and whenever the live task
starts or ends); with it down the poll is the only source and runs on time. Then
``poll_ok()``, when given, is asked when a poll is due: while it says
False the poll waits for a later tick, up to :data:`POLL_HOLD_S` past when it was due. Only a
routine poll waits: a retry after a failed poll runs when its delay is up whatever the hook
says. A hook that raises counts as True. The next poll is ``poll_s`` after the poll actually
ran, so polls are never closer together than without the hook. ``polls_held`` and
``poll_held_max_s`` count the polls that ran late for it and the longest wait. Nothing else
waits for the hook: the history, the heartbeat, the connect and the live connection keep
their timing. Without ``poll_ok`` every poll runs on the first tick it is due, as before.
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
#: The longest a routine poll waits for ``poll_ok`` once it is due (seconds). On the clock the
#: hook says no while a page or scene of the idle show is on or about to begin; in the default
#: config the idle round is home 45 s, page 54 s, home 45 s, scene 36 s, so a held poll waits
#: at most about a minute and this cap matters only for unusual ``[idle]`` shares. Contact
#: stays well inside ``no_data_after_s`` (600 s): a routine poll is at most this late, and a
#: retry after a failed poll is never held.
POLL_HOLD_S = 90.0


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
                 heartbeat_s=HEARTBEAT_S, activity_s=ACTIVITY_S, history_limit=100,
                 connect=None, before_tick=None, after_history=None, poll_ok=None):
        self.client = client
        self.connect = connect or client.connect
        self.before_tick = before_tick
        self.after_history = after_history
        #: ``poll_ok() -> bool``: may a routine poll run now? None: always (see the module).
        self.poll_ok = poll_ok
        #: Polls that ran late because ``poll_ok`` said no, and the longest such wait (s).
        self.polls_held = 0
        self.poll_held_max_s = 0.0
        #: The live subscription is acknowledged and up (``ws_start_ack`` until ``ws_closed``).
        self.live_up = False
        self.last_exc = None
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

    @property
    def history_due(self):
        """A history read has been asked for and not yet made."""
        return self._history_due

    def saw_activity(self, acts):
        """Entries read elsewhere (the source's own fallback read): the next routine read
        starts after the newest of them too."""
        for a in acts:
            if self._newest_activity is None or a.ts > self._newest_activity:
                self._newest_activity = a.ts

    def _fail(self, kind, where, exc, retry_in):
        self.error = kind
        self.last_exc = exc
        self.on_error(kind, where, str(exc), retry_in)

    def _may_poll(self):
        """``poll_ok()``, or True without one; a hook that raises never stops a poll."""
        fn = self.poll_ok
        if fn is None:
            return True
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - the hook must never break the monitor
            return True

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
                await self.connect()
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
            self.live_up = False
            live = asyncio.create_task(self.client.live(self._pushed, self._live_event))
            try:
                await self._watch(stop)
            finally:
                self.connected = False
                self.live_up = False
                live.cancel()
                try:
                    await live
                except BaseException as exc:  # noqa: BLE001 - CancelledError, or its failure
                    self.on_info("live_ended", type(exc).__name__)

    def _live_event(self, event, text):
        """The live connection's events: note whether it is up, then hand each on."""
        if event == "ws_start_ack":
            self.live_up = True
        elif event == "ws_closed":
            self.live_up = False
        self.on_info(event, text)

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
        poll_failing = False  # the last poll attempt failed: the next one is a retry
        held_from = None  # when the due poll was first held back, on ``mono``
        while not stop.is_set():
            now = mono()
            if self.before_tick is not None:
                self.before_tick()
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
                    self.saw_activity(acts)
                    activity_backoff.reset()
                    activity_failing = False
                    self._history_due = False
                    next_activity = mono() + self.activity_s
                    self.on_activity(acts)
                except Exception as exc:  # noqa: BLE001
                    activity_failing = True
                    delay = activity_backoff.next()
                    self.last_exc = exc
                    self.on_error(errors.kind_of(exc), "activity", str(exc), delay)
                    next_activity = mono() + max(delay, self.poll_every)
            if self.after_history is not None:
                await self.after_history()
            if now >= next_poll and (poll_failing or not self.live_up
                                     or now - next_poll >= POLL_HOLD_S or self._may_poll()):
                if held_from is not None:
                    self.polls_held += 1
                    self.poll_held_max_s = max(self.poll_held_max_s, now - held_from)
                    held_from = None
                try:
                    await self.client.refresh_robot()
                    self._contact("poll")
                    poll_backoff.reset()
                    poll_failing = False
                    next_poll = mono() + self.poll_every
                except Exception as exc:  # noqa: BLE001
                    poll_failing = True
                    kind = errors.kind_of(exc)
                    if kind == errors.AUTH:
                        self._fail(kind, "poll", exc, None)
                        return
                    delay = max(poll_backoff.next(), MIN_POLL_S)
                    self._fail(kind, "poll", exc, delay)
                    next_poll = mono() + delay
            elif now >= next_poll and held_from is None:
                held_from = now  # held back: asked again on the next tick
            if now >= next_heartbeat:
                self._contact("heartbeat")
                next_heartbeat = mono() + self.heartbeat_s
            if self._history_due and not activity_failing and (
                    activity_tried is None or mono() >= activity_tried + MIN_POLL_S):
                continue  # read it now, not a tick later; the read clears it or fails
            if await self.sleep(stop, TICK_S):
                return
