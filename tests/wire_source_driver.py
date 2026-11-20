"""Drive a live source through a scripted run with made-up time, on CPython and MicroPython.

Shared by the parity test (``tests/test_wire_source.py``, in-process, for both
``WhiskerSource`` and ``WireSource``) and the on-clock run
(``tests/wire_source_script.py``, a subprocess under ``micropython`` and under CPython).

A scenario's ``events`` happen at made-up times ``t`` (seconds from the run's start). The
source sleeps through :meth:`Clock.sleep`; an event falling inside a sleep is carried out
there, before the source runs on:

- ``probe``: the source's ``view()`` is recorded.
- ``asleep``: the machine sleeps ``s`` seconds: both clocks jump, nothing runs.
- anything else happens at the fake Whisker (a push, a history entry, a dropped socket...):
  ``ask(i, event)`` has the harness do it, and when the event ``expect``s pushed records the
  source must have taken them before time moves on (waited for in real time).

Nothing here talks to anything but the fake on 127.0.0.1.
"""

import asyncio
import sys

from arthur.compat import field_names
from arthur.wire.util import float_text

IS_MPY = sys.implementation.name == "micropython"

#: How long (real seconds) to wait for a pushed record before giving up on it.
EXPECT_TIMEOUT_S = 5.0


def _epoch_dt():
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)


def enc(v):
    """A JSON-ready form that is the same text on both Pythons: floats as their CPython
    text, datetimes in microseconds since 1970, records by field."""
    from datetime import datetime, timedelta

    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return {"f": float_text(v)}
    if isinstance(v, datetime):
        return {"us": (v - _epoch_dt()) // timedelta(microseconds=1)}
    if isinstance(v, (list, tuple)):
        return [enc(x) for x in v]
    if isinstance(v, dict):
        out = {}
        for k in v:
            out[k] = enc(v[k])
        return out
    out = {}
    for n in field_names(type(v)):
        out[n] = enc(getattr(v, n))
    return out


class Counter:
    """Records the source took from the live socket."""

    def __init__(self):
        self.ws = 0

    def wrap(self, fn):
        def counted(record, source):
            try:
                return fn(record, source)
            finally:
                if source == "ws":
                    self.ws += 1
        return counted


class Clock:
    """Monotonic and wall time that move only when the source sleeps."""

    def __init__(self, start_us, events, stop_at, ask, counter, view, on_probe=None):
        self.on_probe = on_probe
        self.start_us = start_us
        self.t = 0.0
        self.stop_at = stop_at
        self.events = []
        for i in range(len(events)):
            self.events.append((events[i]["t"], i, events[i]))
        self.events.sort(key=lambda e: (e[0], e[1]))
        self.ask = ask
        self.counter = counter
        self.view = view
        self.probes = []
        self.notes = []
        self.peak = None  # heap in use, MicroPython (sampled at each event)

    def mono(self):
        return self.t

    def wall(self, t=None):
        from datetime import timedelta

        t = self.t if t is None else t
        return _epoch_dt() + timedelta(microseconds=self.start_us + round(t * 1000000))

    def _sample(self):
        if IS_MPY:
            import gc

            m = gc.mem_alloc()
            if self.peak is None or m > self.peak:
                self.peak = m

    async def _event(self, i, ev):
        do = ev["do"]
        self._sample()
        if do == "probe":
            snap, health = self.view(self.wall())
            probe = {"t": ev["t"], "snap": enc(snap), "health": enc(health)}
            if self.on_probe is not None:
                self.on_probe(probe)  # handed on at once: the run keeps nothing
            else:
                self.probes.append(probe)
            return
        if do == "asleep":
            self.t += ev["s"]
            return
        before = self.counter.ws
        await self.ask(i, ev)
        want = ev.get("expect", 1 if do == "push" else 0)
        if want:
            waited = 0.0
            while self.counter.ws < before + want and waited < EXPECT_TIMEOUT_S:
                await asyncio.sleep(0.005)
                waited += 0.005
            if self.counter.ws < before + want:
                self.notes.append("event " + str(i) + " (" + do + " at " + str(ev["t"])
                                  + "): " + str(self.counter.ws - before) + " of "
                                  + str(want) + " records")

    async def sleep(self, stop, seconds):
        end = self.t + seconds
        while self.events and self.events[0][0] <= end and not stop.is_set():
            at, i, ev = self.events.pop(0)
            self.t = max(self.t, at)
            await self._event(i, ev)
        if not stop.is_set() and end > self.t:
            self.t = end
        if self.stop_at is not None and self.t >= self.stop_at:
            stop.set()
        await asyncio.sleep(0)
        return stop.is_set()


def probes_every(step, until, start=0):
    """Probe events every ``step`` seconds."""
    return [{"t": t, "do": "probe"} for t in range(start, int(until), step)]


class Console:
    """``say(level, text)`` into a list."""

    def __init__(self):
        self.lines = []

    def __call__(self, level, text):
        self.lines.append([level, text])


class ListSink:
    """A sink that keeps the line dicts (in-process runs)."""

    def __init__(self):
        self.states = []
        self.activities = []

    def state(self, line):
        self.states.append(line)

    def activity(self, entry):
        self.activities.append(entry)

    def close(self):
        pass
