"""The weather source against a local fake of Open-Meteo, with fake time. No network."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import web

from arthur.config import WeatherConfig, default_config
from arthur.model import Weather
from arthur.sources import weather as wx
from arthur.sources.weather import WeatherError, WeatherSource, parse_open_meteo

CFG = default_config()
SAMPLE = json.loads((Path(__file__).parent / "data" / "open-meteo-forecast.json").read_text())
T0 = datetime(2026, 11, 18, 14, 42, 53, tzinfo=UTC)


# ---- parsing --------------------------------------------------------------------------------


def test_parses_the_saved_live_response():
    w = parse_open_meteo(SAMPLE, T0)
    assert w == Weather(temp_f=52.5, code=0, is_day=True, high_f=61.9, low_f=38.9, fetched_at=T0)


def test_fetched_at_is_kept_as_utc():
    local = T0.astimezone(CFG.tz)
    assert parse_open_meteo(SAMPLE, local).fetched_at.tzinfo is UTC
    with pytest.raises(ValueError):
        parse_open_meteo(SAMPLE, T0.replace(tzinfo=None))


@pytest.mark.parametrize("change", [
    lambda d: d.pop("daily"),
    lambda d: d["daily"].pop("temperature_2m_max") and d["daily"].pop("temperature_2m_min"),
    lambda d: d["daily"].update(temperature_2m_max=[], temperature_2m_min=[None]),
])
def test_missing_high_or_low_is_none(change):
    data = copy.deepcopy(SAMPLE)
    change(data)
    w = parse_open_meteo(data, T0)
    assert (w.temp_f, w.high_f, w.low_f) == (52.5, None, None)


def test_only_one_of_high_and_low():
    data = copy.deepcopy(SAMPLE)
    del data["daily"]["temperature_2m_min"]
    w = parse_open_meteo(data, T0)
    assert (w.high_f, w.low_f) == (61.9, None)


def test_night_and_a_float_code():
    data = copy.deepcopy(SAMPLE)
    data["current"].update(is_day=0, weather_code=63.0, temperature_2m=-4)
    w = parse_open_meteo(data, T0)
    assert (w.is_day, w.code, w.temp_f) == (False, 63, -4.0)


def _broken(path: str, value) -> dict:
    data = copy.deepcopy(SAMPLE)
    *keys, last = path.split(".")
    target = data
    for k in keys:
        target = target[k]
    if value is KeyError:
        del target[last]
    else:
        target[last] = value
    return data


@pytest.mark.parametrize("data,words", [
    ([1, 2], "not a JSON object"),
    ({"error": True, "reason": "Latitude must be in range"}, "Latitude must be in range"),
    (_broken("current", KeyError), "no 'current'"),
    (_broken("current", [52.5]), "no 'current'"),
    (_broken("current.temperature_2m", KeyError), "temperature_2m"),
    (_broken("current.temperature_2m", "52.5"), "temperature_2m"),
    (_broken("current.temperature_2m", None), "temperature_2m"),
    (_broken("current.weather_code", KeyError), "weather_code"),
    (_broken("current.weather_code", 2.5), "weather_code"),
    (_broken("current.weather_code", -1), "weather_code"),
    (_broken("current.weather_code", True), "weather_code"),
    (_broken("current.is_day", KeyError), "is_day"),
    (_broken("current.is_day", 2), "is_day"),
    (_broken("current.is_day", True), "is_day"),
    (_broken("current_units.temperature_2m", "°C"), "°F"),
    (_broken("daily.temperature_2m_max", 61.9), "temperature_2m_max"),
    (_broken("daily.temperature_2m_min", ["cold"]), "temperature_2m_min"),
])
def test_malformed_responses_raise_a_clear_error(data, words):
    with pytest.raises(WeatherError, match=words):
        parse_open_meteo(data, T0)


def _in_celsius() -> dict:
    data = copy.deepcopy(SAMPLE)
    data["current_units"]["temperature_2m"] = "°C"
    data["current"]["temperature_2m"] = 11.4
    return data


def test_celsius_when_asked_for():
    assert parse_open_meteo(_in_celsius(), T0, "celsius").temp_f == 11.4


def test_fahrenheit_answer_to_a_celsius_request_is_refused():
    with pytest.raises(WeatherError, match="not °C"):
        parse_open_meteo(SAMPLE, T0, "celsius")


# ---- a fake Open-Meteo ----------------------------------------------------------------------


class FakeOpenMeteo:
    """Serves ``replies`` in turn (the last one repeats) and records each request."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict, dict]] = []
        self.replies: list[tuple[int, object]] = [(200, SAMPLE)]
        self.hang = False

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append((dict(request.query), dict(request.headers)))
        if self.hang:
            await asyncio.sleep(3600)
        status, body = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(body, str):
            return web.Response(status=status, text=body)
        return web.json_response(body, status=status)


class FakeTime:
    """Wall time that moves only when the source sleeps; stops it after ``sleeps`` sleeps."""

    def __init__(self, sleeps: int) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self.left = sleeps
        self.on_sleep = None

    def wall(self) -> datetime:
        return T0 + timedelta(seconds=self.t)

    async def sleep(self, stop: asyncio.Event, seconds: float) -> bool:
        self.sleeps.append(seconds)
        self.t += seconds
        if self.on_sleep is not None:
            self.on_sleep(self)
        self.left -= 1
        if self.left <= 0:
            stop.set()
        await asyncio.sleep(0)
        return stop.is_set()


@pytest.fixture
async def fake(aiohttp_client):
    om = FakeOpenMeteo()
    app = web.Application()
    app.router.add_get("/v1/forecast", om.handle)
    client = await aiohttp_client(app)
    om.url = str(client.make_url("/v1/forecast"))
    om.session = client.session
    return om


def make_source(fake, clock: FakeTime, cfg=CFG) -> WeatherSource:
    return WeatherSource(cfg, session=fake.session, url=fake.url, wall_fn=clock.wall,
                         sleep=clock.sleep)


async def run(src: WeatherSource) -> None:
    await asyncio.wait_for(src.run(asyncio.Event()), timeout=10)


async def test_request_parameters_and_no_identifying_header(fake):
    clock = FakeTime(sleeps=1)
    await run(make_source(fake, clock))
    (query, headers), = fake.requests
    assert query == {
        "latitude": "42.36",
        "longitude": "-71.06",
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
        "forecast_days": "1",
    }
    assert headers["User-Agent"] == "arthur-clock"
    sent = {k.lower() for k in headers}
    assert not sent & {"authorization", "cookie", "from", "x-api-key"}
    assert "apikey" not in query and "email" not in query


async def test_fetches_at_start_then_every_refresh(fake, caplog):
    caplog.set_level(logging.INFO)
    clock = FakeTime(sleeps=4)
    src = make_source(fake, clock)
    assert src.view(T0) is None  # nothing before the first fetch
    readings = []
    clock.on_sleep = lambda c: readings.append(src.view(c.wall()))
    await run(src)
    assert len(fake.requests) == 4
    assert clock.sleeps == [900.0] * 4
    # Each reading is dated by when it was fetched.
    assert [r.fetched_at for r in readings] == [T0 + timedelta(seconds=900 * k) for k in range(4)]
    assert src.failures == 0 and not [r for r in caplog.records if r.name == "arthur.weather"]


async def test_failure_keeps_the_last_reading_backs_off_and_recovers(fake, caplog):
    caplog.set_level(logging.INFO)
    fake.replies = [
        (200, SAMPLE),
        (503, "Service Unavailable"),
        (400, {"error": True, "reason": "Parameter 'current' is invalid"}),
        (200, {"current": {}}),
        (200, SAMPLE),
    ]
    clock = FakeTime(sleeps=5)
    src = make_source(fake, clock)
    views = []
    clock.on_sleep = lambda c: views.append(src.view(c.wall()))
    await run(src)
    first = views[0]
    assert first is not None and first.fetched_at == T0
    # Three failures: the first reading is kept as it was, and the waits double from a minute.
    assert views[1:4] == [first] * 3
    assert clock.sleeps == [900.0, 60.0, 120.0, 240.0, 900.0]
    assert views[4].fetched_at > first.fetched_at  # recovered
    assert src.failures == 3 and src.error is None
    fails = [r for r in caplog.records if "weather fetch failed" in r.getMessage()]
    assert len(fails) == 3 and all(r.levelno == logging.WARNING for r in fails)
    assert "HTTP 503" in fails[0].getMessage()
    assert "Parameter 'current' is invalid" in fails[1].getMessage()
    assert "temperature_2m is not a number" in fails[2].getMessage()
    assert caplog.text.count("weather fetched again") == 1


async def test_backoff_caps_and_never_goes_under_a_minute(fake, monkeypatch):
    fake.replies = [(500, "boom")]
    clock = FakeTime(sleeps=7)
    await run(make_source(fake, clock))
    assert clock.sleeps == [60.0, 120.0, 240.0, 480.0, 900.0, 900.0, 900.0]
    assert min(clock.sleeps) >= wx.MIN_INTERVAL_S


async def test_a_short_refresh_is_held_to_a_minute(fake):
    cfg = replace(CFG, weather=replace(CFG.weather, refresh_s=1))  # config forbids it; still
    clock = FakeTime(sleeps=2)
    await run(make_source(fake, clock, cfg))
    assert clock.sleeps == [60.0, 60.0]


async def test_a_reloaded_place_is_used_from_the_next_fetch(fake):
    clock = FakeTime(sleeps=2)
    src = make_source(fake, clock)
    moved = replace(CFG, weather=WeatherConfig(latitude=40.71, longitude=-74.01))
    clock.on_sleep = lambda c: src.update_config(moved)
    await run(src)
    assert [q["latitude"] for q, _ in fake.requests] == ["42.36", "40.71"]


async def test_celsius_is_requested_and_read(fake):
    fake.replies = [(200, _in_celsius())]
    cfg = replace(CFG, weather=replace(CFG.weather, units="celsius"))
    clock = FakeTime(sleeps=1)
    src = make_source(fake, clock, cfg)
    await run(src)
    assert fake.requests[0][0]["temperature_unit"] == "celsius"
    assert src.view(T0).temp_f == 11.4 and src.failures == 0


async def test_a_new_unit_drops_the_reading_at_once(fake):
    clock = FakeTime(sleeps=1)
    src = make_source(fake, clock)
    await run(src)
    assert src.view(T0) is not None
    src.update_config(replace(CFG, weather=replace(CFG.weather, max_age_s=3600)))
    assert src.view(T0) is not None  # same unit: kept
    src.update_config(replace(CFG, weather=replace(CFG.weather, units="celsius")))
    assert src.view(T0) is None  # never a Fahrenheit number shown as Celsius


async def test_no_weather_table_fetches_nothing(fake):
    clock = FakeTime(sleeps=2)
    src = make_source(fake, clock, replace(CFG, weather=None))
    await run(src)
    assert fake.requests == [] and src.view(T0) is None
    assert clock.sleeps == [60.0, 60.0]


async def test_stops_promptly_mid_request(fake):
    fake.hang = True
    src = WeatherSource(CFG, session=fake.session, url=fake.url)  # the real sleep
    stop = asyncio.Event()
    task = asyncio.create_task(src.run(stop))
    while not fake.requests:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1)


async def test_stops_promptly_while_waiting(fake):
    src = WeatherSource(CFG, session=fake.session, url=fake.url)  # waits 900 s for real
    stop = asyncio.Event()
    task = asyncio.create_task(src.run(stop))
    while src.view(T0) is None:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert len(fake.requests) == 1


async def test_owns_and_closes_its_session_when_none_is_given(fake, monkeypatch):
    made = []
    real = wx.aiohttp.ClientSession

    def session(*a, **kw):
        s = real(*a, **kw)
        made.append(s)
        return s

    monkeypatch.setattr(wx.aiohttp, "ClientSession", session)
    clock = FakeTime(sleeps=1)
    src = WeatherSource(CFG, url=fake.url, wall_fn=clock.wall, sleep=clock.sleep)
    await run(src)
    assert src.view(T0) is not None
    assert len(made) == 1 and made[0].closed
