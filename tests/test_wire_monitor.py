"""The wire monitor's policy with fake time: when it connects, polls, reads the history and
backs off, and what it hands on. A stub client stands in for the network, except in the last
tests, which run the real client against the fake Whisker."""

from __future__ import annotations

import asyncio
import inspect

import pytest
from wirefake import activity_entry
from wirehelp import make_client, real_lines, real_raws

from arthur.wire import errors
from arthur.wire.lr4 import parse_activities
from arthur.wire.monitor import Monitor

pytestmark = pytest.mark.usefixtures("loopback_only")

T0 = 1_795_000_000.0


class FakeTime:
    """Monotonic and wall time that move only when the monitor sleeps; events run at their
    time, inside the sleep that passes it (as in ``test_whisker_source``)."""

    def __init__(self):
        self.t = 0.0
        self.stop_at: float | None = None
        self.events: list[tuple[float, int, object]] = []
        self._n = 0
        self.sleeps: list[tuple[float, float]] = []

    def mono(self):
        return self.t

    def now(self):
        return T0 + self.t

    def at(self, t, fn):
        self._n += 1
        self.events.append((t, self._n, fn))
        self.events.sort(key=lambda e: (e[0], e[1]))

    async def sleep(self, stop, seconds):
        self.sleeps.append((self.t, seconds))
        end = self.t + seconds
        while self.events and self.events[0][0] <= end and not stop.is_set():
            at, _, fn = self.events.pop(0)
            self.t = max(self.t, at)
            result = fn()
            if inspect.isawaitable(result):
                await result
        if not stop.is_set():
            self.t = max(self.t, end)
        if self.stop_at is not None and self.t >= self.stop_at:
            stop.set()
        await asyncio.sleep(0)
        return stop.is_set()


class StubClient:
    """Looks like WhiskerClient to the monitor. ``fail[method]`` is a list of exceptions to
    raise on the next calls (None means succeed)."""

    def __init__(self, clock):
        self.clock = clock
        self.calls: list[tuple[float, str, object]] = []
        self.fail: dict[str, list] = {"connect": [], "refresh_robot": [], "history": []}
        self.status = "READY"
        self.activities = parse_activities([activity_entry(T0 - 3600, "robotStatusCatDetect")])
        self.on_update = None

    def _maybe_fail(self, name):
        if self.fail[name]:
            exc = self.fail[name].pop(0)
            if exc is not None:
                raise exc

    async def connect(self):
        self.calls.append((self.clock.t, "connect", None))
        self._maybe_fail("connect")
        return self.record()

    async def refresh_robot(self):
        self.calls.append((self.clock.t, "poll", None))
        self._maybe_fail("refresh_robot")
        return self.record()

    def record(self):
        return {"status": self.status}

    async def history(self, start=None, limit=100):
        self.calls.append((self.clock.t, "history", start))
        self._maybe_fail("history")
        return list(self.activities)

    async def live(self, on_update, on_event=None):
        self.on_update = on_update
        await asyncio.Event().wait()

    def push(self, status):
        self.status = status
        self.on_update()


def make(clock, client, **kw):
    seen = {"records": [], "errors": [], "activity": []}
    mon = Monitor(client, on_record=lambda r, s: seen["records"].append((clock.t, s,
                                                                         r["status"])),
                  on_error=lambda k, w, m, r: seen["errors"].append((clock.t, k, w, r)),
                  on_activity=lambda acts: seen["activity"].append((clock.t, len(acts))),
                  mono=clock.mono, now=clock.now, sleep=clock.sleep, **kw)
    return mon, seen


async def drive(mon, clock, until):
    clock.stop_at = until
    await asyncio.wait_for(mon.run(asyncio.Event()), 10)


def calls(client, name):
    return [t for t, n, _ in client.calls if n == name]


def wire(kind):
    return errors.WireError(kind, "fake " + kind)


@pytest.mark.parametrize("kind, delays", [
    (errors.AUTH, [600, 1200, 2400, 3600, 3600]),
    (errors.API, [30, 60, 120, 240, 480, 600]),
    (errors.TIMEOUT, [30, 60, 120, 240, 480, 600]),
    (errors.NO_ROBOTS, [30, 60, 120, 240, 480, 600]),
])
async def test_connect_failures_back_off_by_kind(kind, delays):
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["connect"] = [wire(kind)] * len(delays)
    mon, seen = make(clock, client)
    await drive(mon, clock, sum(delays) + 1)
    assert [e[3] for e in seen["errors"]] == delays
    assert {e[1] for e in seen["errors"]} == {kind}
    assert len(calls(client, "connect")) == len(delays) + 1
    assert mon.error is None  # the last connect worked and cleared it
    assert seen["records"][0][1] == "start"


async def test_an_os_error_is_api():
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["connect"] = [OSError("no route")]
    mon, seen = make(clock, client)
    await drive(mon, clock, 31)
    assert seen["errors"][0][1:] == ("api", "connect", 30)


async def test_poll_every_five_minutes_with_a_floor_of_two():
    clock = FakeTime()
    client = StubClient(clock)
    mon, _seen = make(clock, client)
    await drive(mon, clock, 1000)
    assert calls(client, "poll") == [300, 600, 900]
    clock2 = FakeTime()
    client2 = StubClient(clock2)
    mon2, _ = make(clock2, client2, poll_s=30)
    await drive(mon2, clock2, 400)
    assert calls(client2, "poll") == [120, 240, 360]


async def test_poll_failures_wait_at_least_120s_and_clear_on_contact():
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["refresh_robot"] = [wire("api"), wire("timeout"), wire("api"), wire("api"),
                                    None]
    mon, seen = make(clock, client)
    await drive(mon, clock, 300 + 120 + 120 + 120 + 240 + 1)
    assert calls(client, "poll") == [300, 420, 540, 660, 900]
    assert [e[3] for e in seen["errors"]] == [120, 120, 120, 240]
    assert [e[1] for e in seen["errors"]] == ["api", "timeout", "api", "api"]
    assert mon.error is None
    assert (900, "poll", "READY") in seen["records"]


async def test_a_push_clears_the_error():
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["refresh_robot"] = [wire("api")]
    mon, seen = make(clock, client)
    clock.at(301, lambda: setattr(clock, "_err", mon.error))
    clock.at(310, lambda: client.push("CAT_DETECTED"))
    await drive(mon, clock, 320)
    assert clock._err == "api"
    assert mon.error is None
    assert (310, "ws", "CAT_DETECTED") in seen["records"]
    assert mon.last_contact == T0 + 310


async def test_a_refused_token_while_polling_goes_back_to_connect():
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["refresh_robot"] = [wire("auth")]
    mon, seen = make(clock, client)
    await drive(mon, clock, 302)
    assert calls(client, "connect") == [0, 300]
    assert seen["errors"][0][1:] == ("auth", "poll", None)


async def test_a_rejected_login_after_that_waits_ten_minutes():
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["refresh_robot"] = [wire("auth")]
    client.fail["connect"] = [None, wire("auth"), wire("auth")]
    mon, _seen = make(clock, client)
    await drive(mon, clock, 300 + 600 + 1200 + 1)
    assert calls(client, "connect") == [0, 300, 900, 2100]


async def test_heartbeat_every_15_minutes_is_not_contact():
    clock = FakeTime()
    client = StubClient(clock)
    mon, seen = make(clock, client, poll_s=10_000)
    await drive(mon, clock, 1801)
    beats = [t for t, s, _ in seen["records"] if s == "heartbeat"]
    assert beats == [900, 1800]
    assert mon.last_contact == T0  # only the start


async def test_history_at_start_then_every_six_hours_from_the_newest_entry():
    clock = FakeTime()
    client = StubClient(clock)
    mon, seen = make(clock, client, poll_s=10_000)
    await drive(mon, clock, 6 * 3600 + 2)
    reads = [(t, s) for t, n, s in client.calls if n == "history"]
    assert reads == [(0, T0 - 72 * 3600), (6 * 3600, T0 - 3600)]
    assert [a[1] for a in seen["activity"]] == [1, 1]


async def test_asked_for_history_never_closer_than_120s():
    clock = FakeTime()
    client = StubClient(clock)
    mon, _seen = make(clock, client, poll_s=10_000)
    clock.at(50, mon.want_history)
    clock.at(500, mon.want_history)
    await drive(mon, clock, 700)
    assert calls(client, "history") == [0, 120, 500]


async def test_history_failures_back_off_above_the_poll():
    clock = FakeTime()
    client = StubClient(clock)
    client.fail["history"] = [wire("api"), wire("api")]
    mon, seen = make(clock, client)
    await drive(mon, clock, 700)
    assert calls(client, "history") == [0, 300, 600]
    assert [e[2] for e in seen["errors"]] == ["activity", "activity"]


# -- with the real client and the fake Whisker ---------------------------------------------


async def test_real_client_through_a_visit_and_a_revoked_token(fake):
    """The monitor with the real client: connect, the real visit pushed live, a poll, the
    history, then a revoked token renewed during the next poll."""
    clock = FakeTime()
    fake.robots = [real_raws()[0]]
    fake.activities = [activity_entry(T0 - 600, "robotStatusCatDetect"),
                       activity_entry(T0 - 540, "catWeight", 4.67)]
    client = make_client(fake)
    mon, seen = make(clock, client)

    async def play():
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        for raw in real_raws():
            await fake.push(raw)
        for _ in range(500):
            if sum(1 for r in seen["records"] if r[1] == "ws") == len(real_lines()):
                return
            await asyncio.sleep(0.01)

    clock.at(5, play)
    clock.at(310, fake.revoke)
    await drive(mon, clock, 620)
    sources = [s for _, s, _ in seen["records"]]
    assert sources[0] == "start"
    assert sources.count("ws") == len(real_lines())
    assert sources.count("poll") == 2
    assert fake.counts["refresh"] == 1
    assert seen["errors"] == []
    assert seen["activity"] == [(0, 2)]
    ws_statuses = [st for _, s, st in seen["records"] if s == "ws"]
    assert ws_statuses == [x["record"]["status"] for x in real_lines()]
