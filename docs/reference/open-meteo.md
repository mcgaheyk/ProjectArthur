# Open-Meteo reference

What the weather source (`arthur/sources/weather.py`) depends on. Collected 2026-11-18 from the
official docs (<https://open-meteo.com/en/docs>, <https://open-meteo.com/en/terms>,
<https://open-meteo.com/en/licence>) and **one live request** the same day. Items marked
**confirmed live** were seen in that response; the saved response is
`tests/data/open-meteo-forecast.json` and the parser's tests are built on it.

## The request
**Confirmed live 2026-11-18 14:42 UTC** (HTTP 200, `Content-Type: application/json;
charset=utf-8`), with Boston's city-centre coordinates:

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=42.36&longitude=-71.06
    &current=temperature_2m,weather_code,is_day
    &daily=temperature_2m_max,temperature_2m_min
    &temperature_unit=fahrenheit
    &timezone=America/New_York
    &forecast_days=1
User-Agent: arthur-clock
```

- No API key, no account. Free use is non-commercial; "personal home automation" is named in
  the terms. A commercial `apikey` exists and we do not use it.
- We send nothing that identifies the owner: the coordinates come from `[weather]` in the
  config, the time zone from `[display] timezone`, and the `User-Agent` is the generic
  `arthur-clock`. No email, no name.
- `timezone` makes the daily values (the high and low) cover the local calendar day, and
  `current.time` local time. Without it the day is UTC's.

## The response (confirmed live)
Top-level shape:

```json
{
  "latitude": 42.365166, "longitude": -71.0618,
  "generationtime_ms": 0.189, "utc_offset_seconds": -18000,
  "timezone": "America/New_York", "timezone_abbreviation": "GMT-5", "elevation": 10.0,
  "current_units": {"time": "iso8601", "interval": "seconds",
                    "temperature_2m": "°F", "weather_code": "wmo code", "is_day": ""},
  "current": {"time": "2026-11-18T09:30", "interval": 900,
              "temperature_2m": 52.5, "weather_code": 0, "is_day": 1},
  "daily_units": {"time": "iso8601", "temperature_2m_max": "°F", "temperature_2m_min": "°F"},
  "daily": {"time": ["2026-11-18"], "temperature_2m_max": [61.9], "temperature_2m_min": [38.9]}
}
```

- The coordinates come back snapped to the model's grid point (42.365166, -71.0618), not as
  sent.
- `current.time` is local time **without an offset**, on a 15-minute step (09:30 for a request
  at 09:42), and `current.interval` is 900 seconds. We do not read it: a reading is dated by
  when we fetched it (`Weather.fetched_at`), and mapping judges its age from that.
- `daily` holds one list per variable, one entry per forecast day; with `forecast_days=1`,
  one entry.

Fields we read (`parse_open_meteo`):

| Field | Becomes | If missing or wrong |
|---|---|---|
| `current.temperature_2m` | `Weather.temp_f` | error |
| `current.weather_code` | `Weather.code` (an integer WMO code) | error |
| `current.is_day` | `Weather.is_day` (1 → day, 0 → night) | error |
| `daily.temperature_2m_max[0]` | `Weather.high_f` | `None` if absent, empty or null; error if not a number |
| `daily.temperature_2m_min[0]` | `Weather.low_f` | same |
| `current_units.temperature_2m` | checked to be `°F` when present | error |

`is_day` per the docs: "1 if the current time step has daylight, 0 at night".

## Errors (docs)
A bad request is HTTP 400 with `{"error": true, "reason": "..."}`. The source logs the reason
and retries; it never shows a reading it could not parse. **Not confirmed live** (we sent no
bad request).

## WMO weather codes (as the docs list them)

| Code | Meaning |
|---|---|
| 0 | Clear sky |
| 1, 2, 3 | Mainly clear, partly cloudy, overcast |
| 45, 48 | Fog, depositing rime fog |
| 51, 53, 55 | Drizzle: light, moderate, dense |
| 56, 57 | Freezing drizzle: light, dense |
| 61, 63, 65 | Rain: slight, moderate, heavy |
| 66, 67 | Freezing rain: light, heavy |
| 71, 73, 75 | Snowfall: slight, moderate, heavy |
| 77 | Snow grains |
| 80, 81, 82 | Rain showers: slight, moderate, violent |
| 85, 86 | Snow showers: slight, heavy |
| 95 | Thunderstorm |
| 96 | Thunderstorm with slight hail |
| 97 | Heavy thunderstorm |
| 99 | Thunderstorm with heavy hail |

The docs say 96 and 99 come only from models with an explicit hail forecast (DWD ICON, UKMO),
so in Boston expect 95 or 97. **97 is in the current docs table** but missing from many older
copies of the WMO list; anything drawing a code must accept every code here and fall back for
an unknown one.

## Limits and licence
- Terms: under 10,000 calls a day for free use (<https://open-meteo.com/en/terms>). The live
  response also carried `X-RateLimit-Limit: 600.0` with `X-RateLimit-Window: 60` (600 calls a
  minute) and `X-RateLimit-Cost: 1.0` per call. We make one call every `refresh_s` (900 s:
  96 a day), retries never faster than once a minute.
- Licence: **CC BY 4.0**. Attribution: "Weather data by Open-Meteo.com", with a link to
  <https://open-meteo.com/>. The clock has no room for it; it belongs in the README or any
  page that shows the data.
- Values come from weather models, not a station; current conditions are "based on
  15-minutely weather model data" (docs).

## How the source uses it
Fetch at start, then every `[weather] refresh_s` (at least 300 in the config; the source holds
it to at least 60). On any failure it keeps the last reading, logs one line, and retries after
60 s, doubling to 900 s, then resets on success. Mapping drops a reading older than
`max_age_s` (2700 s).

## Still unknown
- How often `current` values really change. The response says a 15-minute step and the docs
  say current conditions come from 15-minutely model data, but the underlying models run
  hourly to every six hours, so the same step may be refined between fetches. Not measured.
- Whether `current.time` ever lags well behind the request (a stale model run) and by how
  much. We date readings by fetch time, so a lag would not show.
- Whether `daily` max or min can come back `null` for today (the docs do not say). The parser
  treats it as no high or low.
- Whether the free API ever answers 429 at our rate, and what that body looks like.
