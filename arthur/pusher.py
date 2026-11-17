"""When to push a frame to the clock. Used by the feeder and by scripts/push_test.py.

A frame is pushed when its bytes differ from the last frame pushed, or when ``heartbeat_s``
has passed since the last successful push (so the clock's lifetime, ``ttl_s``, is renewed
on a still screen; D-0004). After a failed push nothing is tried for ``retry_after_s``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from arthur.transports.base import DisplayTransport, FrameLike, TransportError

#: After a failed push, wait this long before trying the clock again.
RETRY_AFTER_S = 1.0


@dataclass(frozen=True)
class PushResult:
    """One attempted push. ``reason`` is "new frame" or "heartbeat"; ``error`` is set when the
    clock refused it or could not be reached."""

    reason: str
    error: TransportError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Pusher:
    """Push bookkeeping for one clock.

    ``reachable`` is None until the first attempt, then whether the last attempt worked;
    ``last_error`` is the last failure's text (kept after a recovery). Compare ``reachable``
    before and after :meth:`offer` to log the start and end of an outage once each.
    """

    def __init__(
        self,
        transport: DisplayTransport,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        retry_after_s: float = RETRY_AFTER_S,
    ) -> None:
        self.transport = transport
        self.time_fn = time_fn
        self.retry_after_s = retry_after_s
        self.last_bytes: bytes | None = None
        self.last_push_t: float | None = None
        self.last_attempt_t: float | None = None
        self.reachable: bool | None = None
        self.last_error: str | None = None
        self.pushes = 0
        self.failures = 0

    def due(self, data: bytes, heartbeat_s: float) -> str | None:
        """Why ``data`` should be pushed now ("new frame" or "heartbeat"), or None."""
        now = self.time_fn()
        if self.reachable is False and self.last_attempt_t is not None and (
            now - self.last_attempt_t < self.retry_after_s
        ):
            return None
        if data != self.last_bytes:
            return "new frame"
        if self.last_push_t is None or now - self.last_push_t >= heartbeat_s:
            return "heartbeat"
        return None

    async def offer(self, frame: FrameLike, ttl_s: float, heartbeat_s: float) -> PushResult | None:
        """Push ``frame`` if it is due. Returns what was attempted, or None. Never raises
        :class:`TransportError`: a failure is in the result and in ``reachable``."""
        data = frame.to_rgb888()
        reason = self.due(data, heartbeat_s)
        if reason is None:
            return None
        self.last_attempt_t = self.time_fn()
        try:
            await self.transport.show(frame, ttl_s)
        except TransportError as exc:
            self.reachable = False
            self.last_error = str(exc)
            self.failures += 1
            return PushResult(reason, exc)
        self.reachable = True
        self.last_bytes = data
        self.last_push_t = self.time_fn()
        self.pushes += 1
        return PushResult(reason)
