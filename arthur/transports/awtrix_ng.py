"""AWTRIX NG 1.2.3 transport: one pushed app whose payload is a full-frame bitmap (D-0003).

This is the only module that knows the clock's HTTP API (docs/reference/awtrix-ng.md). It works
unchanged against a real clock and against the simulator: only ``base_url`` differs.

A TC002 draws pushed apps at double size on a 26x8 grid unless its ``enlargeApps`` setting is
off, which would show only the top-left quarter of our 52x16 frame; so the transport turns that
setting off before it pushes (docs/reference/awtrix-ng.md, "Double size on the TC002"; D-0010).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable

import aiohttp

from arthur.transports.base import FrameLike, TransportError


class AwtrixNgTransport:
    """Pushes frames to ``PUT {base_url}/api/v1/apps/pushed/{app_name}``.

    Each push carries ``lifetimeMs`` and ``lifetimeExpiry: "mark"``, so the clock draws its own
    dark-red stale border if pushes stop (D-0004).

    Before pushing, :meth:`show` makes sure the clock draws pushed apps at native size: it reads
    ``GET /api/v1/settings`` and, if ``enlargeApps`` is true, sends ``PATCH /api/v1/settings
    {"enlargeApps": false}`` (a TC002 enlarges by default; see the module docstring). The check
    is repeated after any failed request (the clock may have restarted or been reset) and when
    more than ``settings_check_s`` has passed since the last good one (so a setting flipped in
    the clock's web UI heals itself). If the check fails, nothing is pushed.
    """

    def __init__(
        self,
        base_url: str,
        app_name: str = "litter",
        session: aiohttp.ClientSession | None = None,
        timeout_s: float = 5,
        settings_check_s: float = 300,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout_s = timeout_s
        self.settings_check_s = settings_check_s
        self.time_fn = time_fn
        self._session = session
        self._own_session = session is None
        #: ``time_fn()`` of the last successful settings check; None means check before pushing.
        self._settings_ok_at: float | None = None

    @staticmethod
    def payload(frame: FrameLike, ttl_s: float) -> dict:
        """The JSON body for one frame (exposed for tests and the studio)."""
        data = base64.b64encode(frame.to_rgb888()).decode("ascii")
        return {
            "draw": [["bitmap", 0, 0, frame.width, frame.height, data]],
            "lifetimeMs": round(ttl_s * 1000),
            "lifetimeExpiry": "mark",
        }

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def _request(self, method: str, path: str, body: dict | None = None) -> str:
        """Send one request; returns the response text. Any failure forgets the settings check."""
        try:
            return await self._send(method, path, body)
        except TransportError:
            self._settings_ok_at = None
            raise

    async def _send(self, method: str, path: str, body: dict | None) -> str:
        url = f"{self.base_url}{path}"
        kwargs: dict = {"timeout": aiohttp.ClientTimeout(total=self.timeout_s)}
        if body is not None:
            kwargs["data"] = json.dumps(body, separators=(",", ":"))
            kwargs["headers"] = {"Content-Type": "application/json"}
        try:
            async with self._get_session().request(method, url, **kwargs) as resp:
                text = await resp.text()
                if not 200 <= resp.status < 300:
                    raise TransportError(f"{method} {url} failed", resp.status, text)
                return text
        except TransportError:
            raise
        except TimeoutError as exc:
            raise TransportError(f"{method} {url} timed out after {self.timeout_s} s") from exc
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError as exc:
            raise TransportError(f"{method} {url}: {exc.__class__.__name__}: {exc}") from exc

    async def _settings(self, method: str, body: dict | None = None) -> dict:
        text = await self._request(method, "/api/v1/settings", body)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise TransportError(f"{method} /api/v1/settings: not JSON", None, text) from exc
        if not isinstance(data, dict):
            raise TransportError(f"{method} /api/v1/settings: not a JSON object", None, text)
        return data

    async def ensure_native_size(self) -> None:
        """Turn ``enlargeApps`` off if the clock has it on. Skipped while the last good check
        is younger than ``settings_check_s``. A clock without the key is left alone."""
        now = self.time_fn()
        if self._settings_ok_at is not None and now - self._settings_ok_at <= self.settings_check_s:
            return
        self._settings_ok_at = None
        settings = await self._settings("GET")
        if settings.get("enlargeApps") is True:
            updated = await self._settings("PATCH", {"enlargeApps": False})
            if updated.get("enlargeApps") is not False:
                raise TransportError("PATCH /api/v1/settings did not turn enlargeApps off",
                                     None, json.dumps(updated))
        self._settings_ok_at = self.time_fn()

    async def show(self, frame: FrameLike, ttl_s: float) -> None:
        await self.ensure_native_size()
        await self._request(
            "PUT", f"/api/v1/apps/pushed/{self.app_name}", self.payload(frame, ttl_s)
        )

    async def clear(self) -> None:
        await self._request("DELETE", f"/api/v1/apps/{self.app_name}")

    async def read_screen(self) -> dict:
        """``GET /api/v1/display/screen``: ``{width, height, pixels: [0xRRGGBB, ...]}``.

        Only the shape is checked. Pixel order and whether brightness is applied are not
        documented (docs/reference/awtrix-ng.md, "Reading the screen back").
        """
        path = "/api/v1/display/screen"
        text = await self._request("GET", path)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise TransportError(f"GET {path}: not JSON", None, text) from exc
        if not (
            isinstance(data, dict)
            and isinstance(data.get("width"), int)
            and isinstance(data.get("height"), int)
            and isinstance(data.get("pixels"), list)
            and all(isinstance(p, int) for p in data["pixels"])
        ):
            raise TransportError(f"GET {path}: not {{width, height, pixels[]}}", None, text)
        return data

    async def close(self) -> None:
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()
