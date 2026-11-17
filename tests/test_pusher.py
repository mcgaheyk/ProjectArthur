"""arthur/pusher.py: when a frame is pushed. A fake transport, fake time."""

from __future__ import annotations

from arthur.pusher import Pusher
from arthur.render import Frame
from arthur.transports import TransportError


class FakeTime:
    def __init__(self) -> None:
        self.t = 50.0

    def __call__(self) -> float:
        return self.t


class FakeTransport:
    def __init__(self) -> None:
        self.shown: list[tuple[bytes, float]] = []
        self.down = False

    async def show(self, frame, ttl_s):
        if self.down:
            raise TransportError("PUT http://clock/api/v1/apps/pushed/litter timed out")
        self.shown.append((frame.to_rgb888(), ttl_s))

    async def clear(self):
        pass

    async def close(self):
        pass


def frame(value: int = 0) -> Frame:
    f = Frame(52, 16)
    f.pixel(0, 0, (value, 0, 0))
    return f


async def test_pushes_on_change_and_heartbeat():
    t, tr = FakeTime(), FakeTransport()
    p = Pusher(tr, time_fn=t)
    assert p.reachable is None
    r = await p.offer(frame(1), 90, 30)
    assert r.reason == "new frame" and r.ok and p.reachable is True and p.pushes == 1
    t.t += 10
    assert await p.offer(frame(1), 90, 30) is None  # same frame, heartbeat not due
    r = await p.offer(frame(2), 90, 30)
    assert r.reason == "new frame"
    t.t += 29.9
    assert await p.offer(frame(2), 90, 30) is None
    t.t += 0.1
    assert (await p.offer(frame(2), 90, 30)).reason == "heartbeat"
    assert [ttl for _, ttl in tr.shown] == [90, 90, 90]


async def test_failure_waits_a_second_then_retries_and_recovers():
    t, tr = FakeTime(), FakeTransport()
    p = Pusher(tr, time_fn=t)
    await p.offer(frame(1), 90, 30)
    tr.down = True
    r = await p.offer(frame(2), 90, 30)
    assert not r.ok and isinstance(r.error, TransportError)
    assert p.reachable is False and "timed out" in p.last_error and p.failures == 1
    t.t += 0.5
    assert await p.offer(frame(2), 90, 30) is None  # inside the 1 s wait
    t.t += 0.5
    assert not (await p.offer(frame(2), 90, 30)).ok  # tried again
    tr.down = False
    t.t += 1
    r = await p.offer(frame(2), 90, 30)
    assert r.ok and r.reason == "new frame" and p.reachable is True
    assert p.pushes == 2 and p.failures == 2
    assert p.last_error is not None  # kept for whoever wants to show it
