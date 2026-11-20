"""Open-Meteo's forecast for the idle show's page (D-0019): the request and the parsing.

Pure and shared: the Mac's weather source (:mod:`arthur.sources.weather`, aiohttp) and the
clock's own (:mod:`arthur.onclock.weather`) ask the same question and read the answer the same
way. Runs unchanged on CPython and MicroPython (shared code: see CLAUDE.md). The request is
in ``docs/reference/open-meteo.md``. No API key, and nothing that identifies the owner: the
coordinates from the config and a generic ``User-Agent``.
"""

from __future__ import annotations

import math
from datetime import UTC
from typing import TYPE_CHECKING

from arthur.compat import IS_MICROPYTHON
from arthur.config import WEATHER_UNITS
from arthur.model import Weather

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any

    from arthur.config import WeatherConfig

if IS_MICROPYTHON:
    _MAPPING = dict
else:
    from collections.abc import Mapping as _MAPPING

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "arthur-clock"
#: Never ask more often than this, even when retrying.
MIN_INTERVAL_S = 60.0
#: Retry after a failure: 60 s, doubling, up to this.
RETRY_MAX_S = 900.0
#: Give up on one request after this long.
TIMEOUT_S = 20.0


class WeatherError(Exception):
    """The forecast could not be fetched or did not have the shape we read."""


def _number(value: Any) -> float | None:
    """A finite JSON number as float; None for anything else (bools are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _first_of_day(daily: Any, key: str) -> float | None:
    """The first value of ``daily[key]``; None when missing, empty or null.
    A value that is there but not a number is malformed."""
    if not isinstance(daily, _MAPPING):
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


def parse_open_meteo(data: Any, fetched_at: datetime, units: str = "fahrenheit") -> Weather:
    """One :class:`Weather` from a forecast response (the request in :func:`request_params`).

    Needs ``current.temperature_2m`` (in ``units``, as requested), ``current.weather_code`` and
    ``current.is_day``;
    the day's high and low are ``None`` when missing. Raises :class:`WeatherError` on an
    error response or a malformed one. ``fetched_at`` must be aware.
    """
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be an aware datetime")
    if not isinstance(data, _MAPPING):
        raise WeatherError(f"response is not a JSON object: {type(data).__name__}")
    if data.get("error"):
        raise WeatherError(f"Open-Meteo error: {data.get('reason', 'no reason given')}")
    current = data.get("current")
    if not isinstance(current, _MAPPING):
        raise WeatherError("response has no 'current' object")
    symbol = WEATHER_UNITS[units]
    answered = data.get("current_units")
    if isinstance(answered, _MAPPING) and answered.get("temperature_2m") not in (None, symbol):
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


def _coordinate(v: float) -> str:
    """``str(v)`` as CPython writes it (MicroPython prints some floats differently)."""
    if not IS_MICROPYTHON:
        return str(v)
    from arthur.wire.util import value_text

    return value_text(v)


def request_params(wc: WeatherConfig, tz_name: str) -> dict[str, str]:
    """The query string for one forecast: current conditions and today's high and low."""
    return {
        "latitude": _coordinate(wc.latitude),
        "longitude": _coordinate(wc.longitude),
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": wc.units,
        "timezone": tz_name,
        "forecast_days": "1",
    }


#: The order the query's keys are written in (MicroPython's dicts keep none).
PARAM_ORDER = ("latitude", "longitude", "current", "daily", "temperature_unit", "timezone",
               "forecast_days")

_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~,"


def _quote(text: str) -> str:
    out = []
    for ch in text:
        if ch in _SAFE:
            out.append(ch)
        else:
            for b in ch.encode():
                out.append("%%%02X" % b)  # noqa: UP031 - the same on MicroPython
    return "".join(out)


def query_string(params: dict[str, str]) -> str:
    """``key=value&...`` in :data:`PARAM_ORDER`, percent-encoded (commas kept)."""
    return "&".join(k + "=" + _quote(params[k]) for k in PARAM_ORDER if k in params)
