"""The clock's own weather (``arthur.onclock.weather.WeatherService``) against a local fake of
Open-Meteo, with fake time: the expectations ``tests/test_weather_source.py`` sets for the
Mac's, case for case where they apply, then verified TLS. No network."""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import web

from arthur.config import WeatherConfig, default_config
from arthur.onclock import weather as ow
from arthur.onclock.clock import Clock
from arthur.onclock.weather import WeatherService
from arthur.sources import openmeteo
from arthur.sources.weather import request_params
from arthur.wire.net import Tls

pytestmark = pytest.mark.usefixtures("loopback_only")

CFG = default_config()
SAMPLE = json.loads((Path(__file__).parent / "data" / "open-meteo-forecast.json").read_text())
T0 = datetime(2026, 11, 18, 14, 42, 53, tzinfo=UTC)


class FakeOpenMeteo:
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


class FakeTime(Clock):
    """Wall time that moves only when the service waits; stops it after ``waits`` waits."""

    def __init__(self, waits: int) -> None:
        self.t = 0.0
        self.waits: list[float] = []
        self.left = waits
        self.on_wait = None
        super().__init__(wall_fn=lambda: T0 + timedelta(seconds=self.t),
                         sleep=self._sleep, time_fn=lambda: self.t)

    async def _sleep(self, s):
        await asyncio.sleep(0)

    async def wait(self, stop, seconds, step=1.0):
        self.waits.append(seconds)
        self.t += seconds
        if self.on_wait is not None:
            self.on_wait(self)
        self.left -= 1
        if self.left <= 0:
            stop.set()
        await asyncio.sleep(0)
        return stop.is_set()


def _app(om: FakeOpenMeteo) -> web.Application:
    app = web.Application()
    app.router.add_get("/v1/forecast", om.handle)
    return app


@pytest.fixture
async def fake(aiohttp_client):
    om = FakeOpenMeteo()
    client = await aiohttp_client(_app(om))
    om.url = str(client.make_url("/v1/forecast"))
    return om


def make(fake, clock, cfg=CFG, tls=None) -> WeatherService:
    logs = []
    src = WeatherService(cfg, tls or Tls(allow_plain=True), clock, url=fake.url,
                         log=lambda level, msg: logs.append((level, msg)))
    src.logs = logs
    return src


async def run(src) -> None:
    await asyncio.wait_for(src.run(asyncio.Event()), timeout=10)


def test_the_query_is_the_mac_sources():
    for cfg in (CFG, replace(CFG, weather=replace(CFG.weather, units="celsius",
                                                  latitude=51.51, longitude=-0.13))):
        src = WeatherService(cfg, Tls(allow_plain=True), FakeTime(1))
        url = src.request_url(cfg.weather)
        query = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        decoded = {k: v.replace("%2F", "/") for k, v in query.items()}
        assert decoded == request_params(cfg.weather, cfg.tz.key)
        assert url.startswith(openmeteo.OPEN_METEO_URL + "?latitude=")


async def test_request_parameters_and_no_identifying_header(fake):
    await run(make(fake, FakeTime(1)))
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


async def test_fetches_at_start_then_every_refresh(fake):
    clock = FakeTime(4)
    src = make(fake, clock)
    assert src.view(T0) is None
    readings = []
    clock.on_wait = lambda c: readings.append(src.view(c.wall()))
    await run(src)
    assert len(fake.requests) == 4
    assert clock.waits == [900.0] * 4
    assert [r.fetched_at for r in readings] == [T0 + timedelta(seconds=900 * k) for k in range(4)]
    assert src.failures == 0 and src.logs == []


async def test_failure_keeps_the_last_reading_backs_off_and_recovers(fake):
    fake.replies = [
        (200, SAMPLE),
        (503, "Service Unavailable"),
        (400, {"error": True, "reason": "Parameter 'current' is invalid"}),
        (200, {"current": {}}),
        (200, SAMPLE),
    ]
    clock = FakeTime(5)
    src = make(fake, clock)
    views = []
    clock.on_wait = lambda c: views.append(src.view(c.wall()))
    await run(src)
    first = views[0]
    assert first is not None and first.fetched_at == T0
    assert views[1:4] == [first] * 3
    assert clock.waits == [900.0, 60.0, 120.0, 240.0, 900.0]
    assert views[4].fetched_at > first.fetched_at
    assert src.failures == 3 and src.error is None
    fails = [m for lv, m in src.logs if "weather fetch failed" in m]
    assert len(fails) == 3 and all(lv == "warning" for lv, m in src.logs if m in fails)
    assert "HTTP 503" in fails[0]
    assert "Parameter 'current' is invalid" in fails[1]
    assert "temperature_2m is not a number" in fails[2]
    assert "retrying in 1m00s" in fails[0] and "keeping the last reading" in fails[0]
    assert [m for _, m in src.logs].count("weather fetched again") == 1


async def test_backoff_caps_and_never_goes_under_a_minute(fake):
    fake.replies = [(500, "boom")]
    clock = FakeTime(7)
    await run(make(fake, clock))
    assert clock.waits == [60.0, 120.0, 240.0, 480.0, 900.0, 900.0, 900.0]


async def test_a_short_refresh_is_held_to_a_minute(fake):
    cfg = replace(CFG, weather=replace(CFG.weather, refresh_s=1))
    clock = FakeTime(2)
    await run(make(fake, clock, cfg))
    assert clock.waits == [60.0, 60.0]


async def test_a_reloaded_place_is_used_from_the_next_fetch(fake):
    clock = FakeTime(2)
    src = make(fake, clock)
    moved = replace(CFG, weather=WeatherConfig(latitude=40.71, longitude=-74.01))
    clock.on_wait = lambda c: src.update_config(moved)
    await run(src)
    assert [q["latitude"] for q, _ in fake.requests] == ["42.36", "40.71"]


async def test_celsius_is_requested_and_read(fake):
    data = json.loads(json.dumps(SAMPLE))
    data["current_units"]["temperature_2m"] = "°C"
    data["current"]["temperature_2m"] = 11.4
    fake.replies = [(200, data)]
    cfg = replace(CFG, weather=replace(CFG.weather, units="celsius"))
    src = make(fake, FakeTime(1), cfg)
    await run(src)
    assert fake.requests[0][0]["temperature_unit"] == "celsius"
    assert src.view(T0).temp_f == 11.4 and src.failures == 0


async def test_a_new_unit_drops_the_reading_at_once(fake):
    src = make(fake, FakeTime(1))
    await run(src)
    assert src.view(T0) is not None
    src.update_config(replace(CFG, weather=replace(CFG.weather, max_age_s=3600)))
    assert src.view(T0) is not None
    src.update_config(replace(CFG, weather=replace(CFG.weather, units="celsius")))
    assert src.view(T0) is None


async def test_no_weather_table_fetches_nothing(fake):
    clock = FakeTime(2)
    src = make(fake, clock, replace(CFG, weather=None))
    await run(src)
    assert fake.requests == [] and src.view(T0) is None
    assert clock.waits == [60.0, 60.0]


async def test_stops_promptly_mid_request(fake):
    fake.hang = True
    src = make(fake, Clock())  # real time
    stop = asyncio.Event()
    task = asyncio.create_task(src.run(stop))
    while not fake.requests:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1)


async def test_stops_promptly_while_waiting(fake):
    src = make(fake, Clock())  # waits 900 s for real
    stop = asyncio.Event()
    task = asyncio.create_task(src.run(stop))
    while src.view(T0) is None:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(fake.requests) == 1


async def test_one_connection_per_fetch_closed_after_it(fake):
    clock = FakeTime(3)
    src = make(fake, clock)
    await run(src)
    assert len(fake.requests) == 3
    assert all(h.get("Connection") == "close" for _, h in fake.requests)


async def test_waits_while_a_scene_is_near(fake):
    clock = FakeTime(1)
    src = make(fake, clock)
    answers = iter([False, False, True])
    asked = []
    src.quiet_fn = lambda: asked.append(clock.t) or next(answers)
    real_sleep = clock.sleep

    async def sleeping(s):
        clock.t += s
        await real_sleep(s)

    clock.sleep = sleeping
    await run(src)
    assert asked == [0.0, 0.5, 1.0] and len(fake.requests) == 1
    assert src.view(T0).fetched_at == T0 + timedelta(seconds=1)


# ---- verified TLS -------------------------------------------------------------------------


@pytest.fixture
async def tls_fake(aiohttp_server, tls_files):
    om = FakeOpenMeteo()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tls_files["cert"], tls_files["key"])
    server = await aiohttp_server(_app(om), ssl=ctx)
    om.url = f"https://127.0.0.1:{server.port}/v1/forecast"
    return om


async def test_over_verified_tls(tls_fake, tls_files):
    src = make(tls_fake, FakeTime(1),
               tls=Tls(ca_file=str(tls_files["ca"]), server_name="localhost"))
    await run(src)
    assert src.view(T0) is not None and src.failures == 0


@pytest.mark.parametrize("case", ["wrong_ca", "wrong_name"])
async def test_tls_that_does_not_verify_is_refused(tls_fake, tls_files, case):
    tls = {"wrong_ca": Tls(ca_file=str(tls_files["wrong_ca"]), server_name="localhost"),
           "wrong_name": Tls(ca_file=str(tls_files["ca"]),
                             server_name="api.open-meteo.com")}[case]
    src = make(tls_fake, FakeTime(1), tls=tls)
    await run(src)
    assert src.view(T0) is None and src.failures == 1
    assert tls_fake.requests == []


def test_backoff_is_the_spikes():
    from arthur.spikelog import Backoff, format_duration

    a, b = ow.Backoff(60, 900), Backoff(60, 900)
    assert [a.next() for _ in range(7)] == [b.next() for _ in range(7)]
    for s in (0, 5.25, 59.9, 60, 61, 899.6, 3600, 7322):
        assert ow._duration(s) == format_duration(s)
