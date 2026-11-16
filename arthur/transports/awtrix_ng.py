"""AWTRIX NG 1.2.3 transport: one pushed app whose payload is a full-frame bitmap (D-0003).

This is the only module that knows the clock's HTTP API (docs/reference/awtrix-ng.md). It works
unchanged against a real clock and against the simulator: only ``base_url`` differs.
"""

from __future__ import annotations

import asyncio
import base64
import json

import aiohttp

from arthur.transports.base import FrameLike, TransportError


class AwtrixNgTransport:
    """Pushes frames to ``PUT {base_url}/api/v1/apps/pushed/{app_name}``.

    Each push carries ``lifetimeMs`` and ``lifetimeExpiry: "mark"``, so the clock draws its own
    dark-red stale border if pushes stop (D-0004).
    """

    def __init__(
        self,
        base_url: str,
        app_name: str = "litter",
        session: aiohttp.ClientSession | None = None,
        timeout_s: float = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout_s = timeout_s
        self._session = session
        self._own_session = session is None

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

    async def _request(self, method: str, path: str, body: dict | None = None) -> None:
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
        except TransportError:
            raise
        except TimeoutError as exc:
            raise TransportError(f"{method} {url} timed out after {self.timeout_s} s") from exc
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError as exc:
            raise TransportError(f"{method} {url}: {exc.__class__.__name__}: {exc}") from exc

    async def show(self, frame: FrameLike, ttl_s: float) -> None:
        await self._request(
            "PUT", f"/api/v1/apps/pushed/{self.app_name}", self.payload(frame, ttl_s)
        )

    async def clear(self) -> None:
        await self._request("DELETE", f"/api/v1/apps/{self.app_name}")

    async def close(self) -> None:
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()
