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
import math
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import aiohttp

from arthur import spikelog as sl
from arthur.config import WEATHER_UNITS, Config, WeatherConfig
from arthur.model import Weather

log = logging.getLogger("arthur.weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "arthur-clock"
#: Never ask more often than this, even when retrying.
MIN_INTERVAL_S = 60.0
#: Retry after a failure: 60 s, doubling, up to this.
RETRY_MAX_S = 900.0
#: Give up on one request after this long.
TIMEOUT_S = 20.0

SleepFn = Callable[[asyncio.Event, float], Awaitable[bool]]


class WeatherError(Exception):
    """The forecast could not be fetched or did not have the shape we read."""


# --------------------------------------------------------------------------------------------
# The response (pure)
# --------------------------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """A finite JSON number as float; None for anything else (bools are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _first_of_day(daily: Any, key: str) -> float | None:
    """The first value of ``daily[key]``; None when missing, empty or null.
    A value that is there but not a number is malformed."""
    if not isinstance(daily, Mapping):
        return None
    values = daily.get(key)
    if values is None:
        return None
    if not isinstance(values, list):
        raise WeatherError(f"daily.{key} is not a list: {values!r}")
    if not values or values[0] is None:
        return None
    v = _number(values[0])
    if v is None:
        raise WeatherError(f"daily.{key}[0] is not a number: {values[0]!r}")
    return v


def parse_open_meteo(data: Mapping[str, Any], fetched_at: datetime,
                     units: str = "fahrenheit") -> Weather:
    """One :class:`Weather` from a forecast response (the request in :func:`request_params`).

    Needs ``current.temperature_2m`` (in ``units``, as requested), ``current.weather_code`` and
    ``current.is_day``;
    the day's high and low are ``None`` when missing. Raises :class:`WeatherError` on an
    error response or a malformed one. ``fetched_at`` must be aware.
    """
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be an aware datetime")
    if not isinstance(data, Mapping):
        raise WeatherError(f"response is not a JSON object: {type(data).__name__}")
    if data.get("error"):
        raise WeatherError(f"Open-Meteo error: {data.get('reason', 'no reason given')}")
    current = data.get("current")
    if not isinstance(current, Mapping):
        raise WeatherError("response has no 'current' object")
    symbol = WEATHER_UNITS[units]
    answered = data.get("current_units")
    if isinstance(answered, Mapping) and answered.get("temperature_2m") not in (None, symbol):
        raise WeatherError(f"temperature is in {answered['temperature_2m']!r}, not {symbol}")

    temp = _number(current.get("temperature_2m"))
    if temp is None:
        raise WeatherError(f"current.temperature_2m is not a number: "
                           f"{current.get('temperature_2m')!r}")
    code = _number(current.get("weather_code"))
    if code is None or code != int(code) or code < 0:
        raise WeatherError(f"current.weather_code is not a WMO code: "
                           f"{current.get('weather_code')!r}")
    is_day = current.get("is_day")
    if isinstance(is_day, bool) or is_day not in (0, 1):
        raise WeatherError(f"current.is_day is not 0 or 1: {is_day!r}")

    daily = data.get("daily")
    return Weather(
        temp_f=temp,
        code=int(code),
        is_day=bool(is_day),
        high_f=_first_of_day(daily, "temperature_2m_max"),
        low_f=_first_of_day(daily, "temperature_2m_min"),
        fetched_at=fetched_at.astimezone(UTC),
    )


def request_params(wc: WeatherConfig, tz_name: str) -> dict[str, str]:
    """The query string for one forecast: current conditions and today's high and low."""
    return {
        "latitude": str(wc.latitude),
        "longitude": str(wc.longitude),
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": wc.units,
        "timezone": tz_name,
        "forecast_days": "1",
    }


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
