"""The display transport interface: how a finished frame reaches a clock (or the simulator)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class FrameLike(Protocol):
    """What a transport needs from a frame: its size and its pixels as RGB888 bytes."""

    width: int
    height: int

    def to_rgb888(self) -> bytes: ...


class TransportError(Exception):
    """The clock refused the request or could not be reached.

    ``status`` is the HTTP status (None for a timeout or connection error) and ``body`` the
    response text, when there was one.
    """

    def __init__(self, message: str, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body

    def __str__(self) -> str:
        msg = super().__str__()
        if self.status is not None:
            msg = f"{msg} (HTTP {self.status})"
        if self.body:
            msg = f"{msg}: {self.body[:300]}"
        return msg


@runtime_checkable
class DisplayTransport(Protocol):
    async def show(self, frame: FrameLike, ttl_s: float) -> None:
        """Show ``frame``; the clock marks it stale if nothing new arrives within ``ttl_s``."""

    async def clear(self) -> None:
        """Remove what this transport put on the clock."""

    async def close(self) -> None:
        """Release connections."""
