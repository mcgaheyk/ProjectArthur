"""When the clock's program pushes a frame: :class:`arthur.pusher.Pusher`'s rules, for both
Pythons.

A frame is pushed when it differs from the last frame pushed, or when ``heartbeat_s`` has
passed since the last successful push (so the clock's lifetime, ``ttl_s``, is renewed on a
still screen; D-0004). After a failed push nothing is tried for ``retry_after_s``.

The one difference: frames are compared by their pixel lists, not as RGB888 bytes, so a beat
whose frame did not change (seven in eight on the home page) encodes nothing. The frame
pushed last is kept by reference; the renderer draws every frame into a new ``Frame``.
"""

from arthur.onclock.awtrix import TransportError

#: After a failed push, wait this long before trying the clock again.
RETRY_AFTER_S = 1.0

NEW_FRAME = "new frame"
HEARTBEAT = "heartbeat"


class PushResult:
    """One attempted push. ``reason`` is "new frame" or "heartbeat"; ``error`` is set when
    the clock refused it or could not be reached."""

    def __init__(self, reason, error=None):
        self.reason = reason
        self.error = error

    @property
    def ok(self):
        return self.error is None


class Pusher:
    """Push bookkeeping for one clock. ``reachable`` is None until the first attempt, then
    whether the last attempt worked; ``last_error`` is the last failure's text."""

    def __init__(self, transport, *, time_fn, retry_after_s=RETRY_AFTER_S):
        self.transport = transport
        self.time_fn = time_fn
        self.retry_after_s = retry_after_s
        self.last_pixels = None
        self.last_push_t = None
        self.last_attempt_t = None
        self.reachable = None
        self.last_error = None
        self.pushes = 0
        self.failures = 0

    def forget(self):
        """Push the next frame whatever it is (after a pause, say)."""
        self.last_pixels = None

    def due(self, frame, heartbeat_s):
        """Why ``frame`` should be pushed now ("new frame" or "heartbeat"), or None."""
        now = self.time_fn()
        if self.reachable is False and self.last_attempt_t is not None and (
            now - self.last_attempt_t < self.retry_after_s
        ):
            return None
        if self.last_pixels is None or frame.pixels != self.last_pixels:
            return NEW_FRAME
        if self.last_push_t is None or now - self.last_push_t >= heartbeat_s:
            return HEARTBEAT
        return None

    async def offer(self, frame, ttl_s, heartbeat_s):
        """Push ``frame`` if it is due. Returns what was attempted, or None. Never raises
        :class:`TransportError`: a failure is in the result and in ``reachable``."""
        reason = self.due(frame, heartbeat_s)
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
        self.last_pixels = frame.pixels
        self.last_push_t = self.time_fn()
        self.pushes += 1
        return PushResult(reason)
