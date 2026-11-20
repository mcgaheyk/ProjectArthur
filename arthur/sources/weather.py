"""The weather for the idle show's page (D-0019), from Open-Meteo's forecast API.

:class:`WeatherSource` fetches one reading at start and then every ``[weather] refresh_s``,
and answers :meth:`WeatherSource.view` with the latest reading as it is; mapping decides
whether it is fresh enough to show (``max_age_s``). It is a decoration: on any failure it keeps
the last reading, logs one line and retries with backoff (60 s doubling to 900 s, never faster
than once a minute), and it never raises out of :meth:`WeatherSource.run`.

The request is in ``docs/reference/open-meteo.md``. No API key, and nothing that identifies
the owner: the coordinates from the config and a generic ``User-Agent``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

import aiohttp

from arthur import spikelog as sl
from arthur.config import Config, WeatherConfig
from arthur.model import Weather

# The request and the parsing are shared with the clock's own weather source.
from arthur.sources.openmeteo import (
    MIN_INTERVAL_S,
    OPEN_METEO_URL,
    RETRY_MAX_S,
    TIMEOUT_S,
    USER_AGENT,
    WeatherError,
    parse_open_meteo,
    request_params,
)

log = logging.getLogger("arthur.weather")

SleepFn = Callable[[asyncio.Event, float], Awaitable[bool]]


# --------------------------------------------------------------------------------------------
# The source
# --------------------------------------------------------------------------------------------


class WeatherSource:
    """Run :meth:`run` as a task; call :meth:`view` as often as you like."""

    def __init__(
        self,
        cfg: Config,
        *,
        session: aiohttp.ClientSession | None = None,
        url: str = OPEN_METEO_URL,
        wall_fn: Callable[[], datetime] | None = None,
        sleep: SleepFn | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self.cfg = cfg
        self.url = url
        self._session = session
        self._wall = wall_fn or (lambda: datetime.now(UTC))
        self._sleep: SleepFn = sleep or sl._sleep_or_stop
        self.timeout_s = timeout_s
        self.reading: Weather | None = None
        self.error: str | None = None
        self.fetches = 0
        self.failures = 0

    def view(self, now: datetime) -> Weather | None:
        """The latest reading, however old; None before the first, or with no ``[weather]``."""
        if self.cfg.weather is None:
            return None
        return self.reading

    def update_config(self, cfg: Config) -> None:
        """Take a reloaded config. A new place or refresh applies from the next fetch; a new
        unit drops the reading at once, so a number is never shown in the wrong unit."""
        old, new = self.cfg.weather, cfg.weather
        if old is not None and new is not None and old.units != new.units:
            self.reading = None
        self.cfg = cfg

    async def fetch(self, session: aiohttp.ClientSession, wc: WeatherConfig) -> Weather:
        """One request and its reading. Raises on any failure."""
        params = request_params(wc, self.cfg.tz.key)
        self.fetches += 1
        async with session.get(
            self.url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=self.timeout_s),
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except ValueError as exc:
                raise WeatherError(f"HTTP {resp.status}, body is not JSON") from exc
            if resp.status != 200:
                reason = data.get("reason") if isinstance(data, Mapping) else None
                raise WeatherError(f"HTTP {resp.status}: {reason or 'no reason given'}")
        return parse_open_meteo(data, self._wall(), wc.units)

    async def run(self, stop: asyncio.Event) -> None:
        """Fetch now, then every ``refresh_s``, until ``stop``. Never returns on failure."""
        own = self._session is None
        session = self._session or aiohttp.ClientSession()
        backoff = sl.Backoff(MIN_INTERVAL_S, RETRY_MAX_S)
        try:
            while not stop.is_set():
                wc = self.cfg.weather
                if wc is None:  # [weather] was removed: nothing to fetch, look again later
                    delay = MIN_INTERVAL_S
                else:
                    try:
                        reading = await sl._until_stop(stop, self.fetch(session, wc))
                    except sl._Stopped:
                        return
                    except Exception as exc:  # noqa: BLE001 - a decoration: keep going
                        delay = max(backoff.next(), MIN_INTERVAL_S)
                        self.failures += 1
                        self.error = f"{type(exc).__name__}: {exc}"
                        kept = "keeping the last reading" if self.reading else "no reading yet"
                        log.warning("weather fetch failed: %s; %s; retrying in %s",
                                    self.error, kept, sl.format_duration(delay))
                    else:
                        self.reading = reading
                        backoff.reset()
                        delay = max(float(wc.refresh_s), MIN_INTERVAL_S)
                        if self.error is not None:
                            log.info("weather fetched again")
                        self.error = None
                if await self._sleep(stop, delay):
                    return
        finally:
            if own:
                with contextlib.suppress(Exception):
                    await session.close()
